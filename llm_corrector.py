from __future__ import annotations

import json
import re
from typing import Any

import requests

from config import get_int_setting, get_setting


DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_TOKENS = 4096


HTTP_ERROR_MESSAGES = {
    401: "大模型认证失败，请检查 LLM_API_KEY 或 DEEPSEEK_API_KEY 是否正确。",
    402: "DeepSeek 账户余额不足或计费状态异常，请检查控制台余额。",
    429: "大模型接口请求过于频繁，请稍后重试或降低并发数。",
    500: "大模型服务暂时异常，请稍后重试。",
    503: "大模型服务繁忙或不可用，请稍后重试。",
}


class LLMConfigError(RuntimeError):
    pass


def _mock_correction(ocr_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "score": 82,
        "summary": "共识别 2 道题，基础计算思路基本清楚，但第 1 题结果出现偏差，需要加强验算习惯。",
        "comments": "整体书写较完整，能呈现主要步骤。第 1 题计算结果错误；第 2 题答案和过程正确。",
        "suggestions": "建议每道题完成后用逆运算或代入法检查答案，重点复习加减乘除的基础计算和单位书写。",
        "score_breakdown": "第 1 题 32/50，第 2 题 50/50，总分 82/100。",
        "strengths": ["能写出主要解题步骤", "第 2 题计算过程完整"],
        "weaknesses": ["第 1 题基础计算失误", "做题后缺少验算"],
        "next_steps": ["每天练习 5 道基础计算题", "完成后用逆运算检查结果", "批改订正时写出错因"],
        "questions": [
            {
                "question_no": "1",
                "is_correct": False,
                "score": 32,
                "max_score": 50,
                "student_answer": "9",
                "correct_answer": "8",
                "analysis": "题目考查基础加法。学生列式方向正确，但最终计算结果写成 9。",
                "comment": "计算结果错误，请重新检查加法过程。",
                "deduction_reason": "列式思路正确，但最终计算结果错误，扣除结果分。",
                "solution_steps": [
                    "先确定题目要求计算 3 + 5。",
                    "把 3 和 5 相加，得到 8。",
                    "因此正确答案是 8。",
                ],
                "mistake_analysis": "错误主要来自基础加法计算失误，可能是心算过快，没有进行复查。",
                "knowledge_points": ["一位数加法", "计算验算"],
                "revision_advice": "订正时重新写出 3 + 5 = 8，并用 8 - 5 = 3 进行验算。",
                "confidence": "high",
                "uncertain_reason": "",
            },
            {
                "question_no": "2",
                "is_correct": True,
                "score": 50,
                "max_score": 50,
                "student_answer": "4",
                "correct_answer": "4",
                "analysis": "题目考查平均分或除法计算。学生答案与正确结果一致。",
                "comment": "答案正确，继续保持。",
                "deduction_reason": "",
                "solution_steps": [
                    "根据题意列式 12 / 3。",
                    "12 平均分成 3 份，每份是 4。",
                    "所以答案为 4。",
                ],
                "mistake_analysis": "本题未发现明显错误。",
                "knowledge_points": ["除法意义", "平均分"],
                "revision_advice": "保持当前步骤书写习惯，可以继续练习同类除法题。",
                "confidence": "high",
                "uncertain_reason": "",
            },
        ],
    }


def _prompt_for(ocr_result: dict[str, Any]) -> str:
    ocr_text = ocr_result.get("ocr_text", "")
    questions = ocr_result.get("questions", [])
    return f"""
你是一名严谨、耐心的中小学作业批改老师。请根据 OCR 识别结果批改学生作业，输出详细但可控的逐题批改。

批改要求：
1. 按 OCR 结构化题目逐题批改，不要漏题；如果 OCR 把多道题合并到同一块，请尽量在该块内区分题目。
2. 总分使用 0-100 分制。每题满分由你根据题目数量、难度和步骤复杂度自动分配，所有题目满分合计约为 100。
3. 每题必须给出题目理解、学生答案、是否正确、本题得分、满分、扣分原因、正确答案、详细题解、错因分析、订正建议和相关知识点。
4. is_correct、score、max_score 必须一致：满分且无不确定时 is_correct 才能为 true；部分正确或需扣分时 score 必须小于 max_score。
5. 少写一个必填答案、缺少读作/单位/步骤、过程缺失或 OCR 无法确认完整过程时不能给满分，必须写明 deduction_reason。
6. 题解要具体到关键步骤，但不要过度冗长；每题 solution_steps 建议 3-6 步。
7. 如果 OCR 内容不完整或看不清，不要编造。请降低 confidence，并在 uncertain_reason 中说明不确定原因。
8. 必须只返回 JSON，不要输出 Markdown、代码块或额外解释。

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
  "score_breakdown": "评分构成说明，例如第1题 30/40，第2题 45/60，总分75/100",
  "strengths": ["学生做得好的地方"],
  "weaknesses": ["主要薄弱点"],
  "next_steps": ["下一步学习建议"],
  "questions": [
    {{
      "question_no": "1",
      "is_correct": true,
      "score": 40,
      "max_score": 40,
      "question_understanding": "本题考查内容和题意理解",
      "student_answer": "学生答案",
      "correct_answer": "正确答案",
      "analysis": "解题分析",
      "comment": "简短批注",
      "deduction_reason": "扣分原因，答对可为空",
      "solution_steps": ["步骤1", "步骤2", "步骤3"],
      "mistake_analysis": "错因分析，答对也可说明为什么正确",
      "knowledge_points": ["知识点1", "知识点2"],
      "revision_advice": "订正建议",
      "confidence": "high",
      "uncertain_reason": ""
    }}
  ]
}}
""".strip()


def _strict_json_retry_prompt(ocr_result: dict[str, Any]) -> str:
    return (
        "上一次回答没有形成可解析的 JSON。请重新批改，并且只输出一个合法 JSON 对象，"
        "不要包含 Markdown、代码块、解释文字或多余前后缀。\n\n"
        f"{_prompt_for(ocr_result)}"
    )


def _parse_json_response(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("大模型返回内容为空。")

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
    questions = [item for item in questions if isinstance(item, dict)]

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
        "score_breakdown": payload.get("score_breakdown", ""),
        "strengths": payload.get("strengths", []),
        "weaknesses": payload.get("weaknesses", []),
        "next_steps": payload.get("next_steps", []),
        "questions": questions,
    }


def _is_deepseek_base_url(base_url: str) -> bool:
    return "deepseek.com" in base_url.lower()


def _build_payload(
    *,
    model: str,
    ocr_result: dict[str, Any],
    max_tokens: int,
    retry_json: bool = False,
    deepseek_thinking: str = "disabled",
) -> dict[str, Any]:
    user_prompt = _strict_json_retry_prompt(ocr_result) if retry_json else _prompt_for(ocr_result)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只返回严格 JSON，不输出 Markdown 或解释文字。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    if deepseek_thinking:
        payload["thinking"] = {"type": deepseek_thinking}

    return payload


def _raise_for_llm_error(response: requests.Response) -> None:
    if response.status_code < 400:
        return

    message = HTTP_ERROR_MESSAGES.get(response.status_code)
    if message:
        raise RuntimeError(message)

    try:
        detail = response.json().get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        detail = response.text[:300]

    suffix = f"：{detail}" if detail else ""
    raise RuntimeError(f"大模型接口调用失败，HTTP {response.status_code}{suffix}")


def _request_correction(
    *,
    base_url: str,
    api_key: str,
    model: str,
    ocr_result: dict[str, Any],
    max_tokens: int,
    retry_json: bool = False,
) -> dict[str, Any]:
    deepseek_thinking = ""
    if _is_deepseek_base_url(base_url):
        deepseek_thinking = (get_setting("DEEPSEEK_THINKING", "disabled") or "disabled").lower()

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=_build_payload(
                model=model,
                ocr_result=ocr_result,
                max_tokens=max_tokens,
                retry_json=retry_json,
                deepseek_thinking=deepseek_thinking,
            ),
            timeout=90,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"大模型接口请求失败：{exc}") from exc

    _raise_for_llm_error(response)
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("大模型返回结构不符合 Chat Completions 格式。") from exc

    return _normalize_correction(_parse_json_response(content))


def correct_homework(ocr_result: dict[str, Any]) -> dict[str, Any]:
    mode = (get_setting("LLM_MODE", "api") or "api").lower()
    if mode == "mock":
        return _mock_correction(ocr_result)

    api_key = get_setting("LLM_API_KEY") or get_setting("DEEPSEEK_API_KEY")
    base_url = (get_setting("LLM_BASE_URL", DEFAULT_LLM_BASE_URL) or "").rstrip("/")
    model = get_setting("LLM_MODEL", DEFAULT_LLM_MODEL)
    max_tokens = max(256, get_int_setting("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS))

    if not api_key:
        raise LLMConfigError(
            "未配置 LLM_API_KEY 或 DEEPSEEK_API_KEY。可以在 .env 或 Streamlit Secrets 中配置，"
            "或将 LLM_MODE 设置为 mock 先跑通演示流程。"
        )

    try:
        return _request_correction(
            base_url=base_url,
            api_key=api_key,
            model=model,
            ocr_result=ocr_result,
            max_tokens=max_tokens,
        )
    except ValueError:
        return _request_correction(
            base_url=base_url,
            api_key=api_key,
            model=model,
            ocr_result=ocr_result,
            max_tokens=max_tokens,
            retry_json=True,
        )
