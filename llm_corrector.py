from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from config import DEBUG_DIR, get_int_setting, get_setting


DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_CONSISTENCY_RETRIES = 2

HTTP_ERROR_MESSAGES = {
    401: "大模型认证失败，请检查 LLM_API_KEY 或 DEEPSEEK_API_KEY 是否正确。",
    402: "DeepSeek 账户余额不足或计费状态异常，请检查控制台余额。",
    429: "大模型接口请求过于频繁，请稍后重试或降低并发数。",
    500: "大模型服务暂时异常，请稍后重试。",
    503: "大模型服务繁忙或不可用，请稍后重试。",
}


class LLMConfigError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# OCR 数据清洗（与原有逻辑完全一致）
# ---------------------------------------------------------------------------


def _collect_text_values(payload: Any) -> list[str]:
    if isinstance(payload, str):
        text = payload.strip()
        return [text] if text else []

    if isinstance(payload, list):
        parts: list[str] = []
        for item in payload:
            parts.extend(_collect_text_values(item))
        return parts

    if not isinstance(payload, dict):
        return []

    parts: list[str] = []
    text = payload.get("Text")
    if isinstance(text, str) and text.strip():
        parts.append(text.strip())

    for key in ("Question", "Option", "Figure", "Table", "Parse", "ResultList"):
        parts.extend(_collect_text_values(payload.get(key)))

    return parts


def _collect_answer_values(payload: Any) -> list[str]:
    if isinstance(payload, list):
        parts: list[str] = []
        for item in payload:
            parts.extend(_collect_answer_values(item))
        return parts

    if not isinstance(payload, dict):
        return []

    parts: list[str] = []
    for key, value in payload.items():
        if key.lower() == "answer":
            parts.extend(_collect_text_values(value))
        else:
            parts.extend(_collect_answer_values(value))
    return parts


def _dedupe_join(parts: list[str]) -> str:
    seen = set()
    unique_parts = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        unique_parts.append(part)
    return "\n".join(unique_parts)


def _prepare_questions_for_llm(questions: list[Any]) -> list[dict[str, Any]]:
    """将腾讯云 OCR 返回的原始题目结构清洗为 LLM 可用的格式。"""
    prepared: list[dict[str, Any]] = []
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            continue

        structured_parts: list[str] = []
        for key in ("question", "option", "figure", "table"):
            structured_parts.extend(_collect_text_values(item.get(key)))

        recognized_text = _dedupe_join(structured_parts)
        if not recognized_text:
            recognized_text = str(item.get("text", "")).strip()

        student_answer_area = _dedupe_join(_collect_answer_values(item))

        prepared.append(
            {
                "question_no": str(item.get("question_no") or item.get("subject_index") or index),
                "recognized_text": recognized_text,
                "student_answer_area": student_answer_area,
                "source": item.get("source") or "ocr",
            }
        )

    return prepared


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Prompt：阶段一 — 推导正确答案（只负责"算答案"）
# ---------------------------------------------------------------------------


def _derive_answers_prompt(ocr_result: dict[str, Any]) -> str:
    """构建「推导答案」阶段的 prompt。

    这个阶段只让 LLM 做一件事：理解题目并推导出标准正确答案。
    不做评分、不做比对、不写学生反馈。
    """
    ocr_text = ocr_result.get("ocr_text", "")
    raw_questions = ocr_result.get("questions", [])
    questions = _prepare_questions_for_llm(raw_questions if isinstance(raw_questions, list) else [])

    return f"""
你是一名严谨的中小学数学教师。请根据 OCR 识别结果逐题分析，独立推导出每道题的标准正确答案。

输入说明：
- OCR 原文是整页识别文本，仅用于辅助理解版面。
- recognized_text 是题干、选项或卷面文字。
- student_answer_area 是 OCR 从作答区域识别到的学生手写内容；**它不是标准答案**，不能直接采用。
- Answer/answer/ResultList.Answer 字段都是学生作答，不是标准答案。

你的任务（只做这些，不要评分）：
1. 仔细阅读每道题，明确题目在问什么。
2. 独立计算/推导出标准正确答案。不要参考 student_answer_area。
3. 按题目难度和步骤复杂度分配满分值，所有题目的 max_score 之和即为卷面总分。
4. 给出简明的解题步骤和涉及的知识点。

重要规则：
- 口算题逐项验算，每个算式都要算对。
- 数位题必须按"亿位、千万位、百万位、十万位、万位、千位、百位、十位、个位"的位值独立构造数字和读法。写作和读作必须互相匹配。
- 如果 OCR 内容不完整或看不清，在 uncertain_note 中说明，不要编造。
- **只返回 JSON，不要输出 Markdown、代码块或解释文字。**

OCR 原文：
{ocr_text}

OCR 结构化题目（student_answer_area 是学生作答，不能当作标准答案）：
{json.dumps(questions, ensure_ascii=False)}

返回 JSON 格式：
{{{{
  "questions": [
    {{{{
      "question_no": "1",
      "question_understanding": "本题包含 6 道口算：25×4、360÷90、125×8、480÷60、36×2、810÷9",
      "correct_answer": "25×4=100，360÷90=4，125×8=1000，480÷60=8，36×2=72，810÷9=90",
      "max_score": 40,
      "solution_steps": ["25×4=100", "360÷90=4（因为 90×4=360）", "125×8=1000", "480÷60=8（因为 60×8=480）", "36×2=72", "810÷9=90"],
      "knowledge_points": ["整数乘法", "整数除法", "口算验算"],
      "uncertain_note": ""
    }}}}
  ]
}}}}
""".strip()


# ---------------------------------------------------------------------------
# Prompt：阶段二 — 批改评分（拿到标准答案后，只负责"比对+打分+写反馈"）
# ---------------------------------------------------------------------------


def _grade_prompt(
    ocr_result: dict[str, Any],
    derived_answers: list[dict[str, Any]],
) -> str:
    """构建「批改评分」阶段的 prompt。

    这个阶段 LLM 拿到已验证的标准答案，只需做：
    - 对比学生作答和标准答案
    - 逐题打分
    - 写评语和反馈
    """
    ocr_text = ocr_result.get("ocr_text", "")
    raw_questions = ocr_result.get("questions", [])
    questions = _prepare_questions_for_llm(raw_questions if isinstance(raw_questions, list) else [])

    # 把标准答案格式化成易读的文本
    answers_lines: list[str] = []
    for item in derived_answers:
        qno = item.get("question_no", "?")
        understanding = item.get("question_understanding", "")
        correct = item.get("correct_answer", "")
        max_s = item.get("max_score", 0)
        steps = item.get("solution_steps", [])
        kp = item.get("knowledge_points", [])
        uncertain = item.get("uncertain_note", "")

        answers_lines.append(
            f"第{qno}题：\n"
            f"  题目理解：{understanding}\n"
            f"  标准正确答案：{correct}\n"
            f"  满分：{max_s}\n"
            f"  解题步骤：{'；'.join(steps) if steps else '（无）'}\n"
            f"  知识点：{', '.join(kp) if kp else '（无）'}"
            + (f"\n  注意：{uncertain}" if uncertain else "")
        )

    answers_block = "\n\n".join(answers_lines)

    return f"""
你是一名严谨、耐心的中小学作业批改老师。请根据已验证的标准答案批改学生作业。

=== 已验证的标准答案（请**直接使用**，不要修改或重新计算） ===

{answers_block}

=== 批改要求 ===

1. **逐题比对**：将学生作答与上述标准答案逐项对比，不要遗漏。
2. **不要重新计算答案**：上面给出的 correct_answer 已经过验证，你只需判断学生答案是否正确。
3. **评分一致性**：
   - is_correct、score、max_score、deduction_reason 必须自洽
   - 有扣分原因时 is_correct 必须为 false，score 必须 < max_score
   - score = max_score 时 deduction_reason 必须为空，comment 不能指出错误
   - score < max_score 时必须写明 deduction_reason 或 uncertain_reason
4. **正确的题目**：is_correct=true，score=max_score，deduction_reason=""，comment 正向鼓励
5. **错误的题目**：is_correct=false，score<max_score，deduction_reason 写清扣分原因
6. **不确定的题目**：OCR 不清或学生作答无法辨认时降低 confidence，uncertain_reason 说明原因，且不能给满分
7. **总分必须等于逐题得分之和**：score 字段的值必须恰好等于所有题目 score 之和。例如第1题得 30、第2题得 45，则 score 必须是 75。score_breakdown 格式如"第1题 30/40，第2题 45/60，总分 75/100"。

=== OCR 原文（辅助理解） ===

{ocr_text}

=== OCR 结构化题目（student_answer_area 是学生作答） ===

{json.dumps(questions, ensure_ascii=False)}

=== 返回 JSON 格式 ===

{{{{
  "score": 85,
  "summary": "整体表现概述",
  "comments": "总体批注",
  "suggestions": "学习建议",
  "score_breakdown": "第1题 30/40，第2题 45/60，总分75/100",
  "strengths": ["做得好的地方"],
  "weaknesses": ["主要薄弱点"],
  "next_steps": ["下一步学习建议"],
  "questions": [
    {{{{
      "question_no": "1",
      "is_correct": false,
      "score": 32,
      "max_score": 40,
      "student_answer": "学生具体作答内容",
      "correct_answer": "（必须与上面给出的标准答案一致）",
      "analysis": "分析学生的作答思路和错误原因",
      "comment": "给学生的批注",
      "deduction_reason": "扣分原因（满分时为空）",
      "solution_steps": ["解题步骤"],
      "mistake_analysis": "错误分析",
      "knowledge_points": ["知识点"],
      "revision_advice": "订正建议",
      "confidence": "high",
      "uncertain_reason": ""
    }}}}
  ]
}}}}
""".strip()


def _grade_json_retry_prompt(
    ocr_result: dict[str, Any],
    derived_answers: list[dict[str, Any]],
) -> str:
    return (
        "上一次回答没有形成可解析的 JSON。请重新批改，并且只输出一个合法 JSON 对象，"
        "不要包含 Markdown、代码块、解释文字或多余前后缀。\n\n"
        f"{_grade_prompt(ocr_result, derived_answers)}"
    )


def _grade_consistency_retry_prompt(
    ocr_result: dict[str, Any],
    derived_answers: list[dict[str, Any]],
    issues: list[str],
    previous_correction: dict[str, Any] | None = None,
) -> str:
    issue_text = "\n".join(f"- {issue}" for issue in issues[:10])
    previous_text = ""
    if previous_correction:
        previous_text = (
            "\n上一次批改 JSON：\n"
            f"{json.dumps(previous_correction, ensure_ascii=False)}\n"
        )

    return (
        "上一次批改结果的 JSON 字段内部不一致。请根据标准答案和上一次 JSON，"
        "定向修正下列矛盾后返回完整 JSON。不要沿用矛盾字段；不要输出 Markdown 或解释文字。\n"
        f"{issue_text}\n\n"
        "修正规则：\n"
        "- 总分 score 必须恰好等于所有题目 score 之和\n"
        "- 有扣分原因或不确定原因时不能满分\n"
        "- 指出错误时 is_correct 必须为 false\n"
        "- is_correct 为 true 时必须满分且 deduction_reason/uncertain_reason 为空\n"
        "- deduction_reason 非空时必须 is_correct=false 且 score < max_score\n"
        "- score < max_score 时必须写明 deduction_reason 或 uncertain_reason\n"
        "- correct_answer 必须与标准答案一致，不能包含自我纠错文字\n"
        f"{previous_text}\n"
        f"{_grade_prompt(ocr_result, derived_answers)}"
    )


# ---------------------------------------------------------------------------
# JSON 解析 & 标准化
# ---------------------------------------------------------------------------


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


def _parse_derived_answers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """解析并标准化「推导答案」阶段的 LLM 返回。"""
    questions = payload.get("questions")
    if not isinstance(questions, list):
        questions = []

    result: list[dict[str, Any]] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "question_no": str(item.get("question_no", "")),
                "question_understanding": str(item.get("question_understanding", "")),
                "correct_answer": str(item.get("correct_answer", "")),
                "max_score": _to_int(item.get("max_score"), 10),
                "solution_steps": (
                    item.get("solution_steps")
                    if isinstance(item.get("solution_steps"), list)
                    else []
                ),
                "knowledge_points": (
                    item.get("knowledge_points")
                    if isinstance(item.get("knowledge_points"), list)
                    else []
                ),
                "uncertain_note": str(item.get("uncertain_note", "")),
            }
        )

    return result


def _normalize_correction(payload: dict[str, Any]) -> dict[str, Any]:
    """标准化「批改评分」阶段的 LLM 返回。

    总分以逐题得分之和为准，不信任 LLM 声明的 score 字段。
    满分由阶段一的 max_score 决定，不强制为 100。
    """
    questions = payload.get("questions")
    if not isinstance(questions, list):
        questions = []
    questions = [item for item in questions if isinstance(item, dict)]

    # 从逐题得分重算总分（LLM 定的满分是多少就是多少）
    total = sum(_to_int(q.get("score"), 0) for q in questions)

    # 自动生成 score_breakdown（如果 LLM 没给或给的明显不对）
    llm_breakdown = str(payload.get("score_breakdown", ""))
    if llm_breakdown and "总分" in llm_breakdown:
        score_breakdown = llm_breakdown
    else:
        parts = [
            f"第{q.get('question_no', '?')}题 {_to_int(q.get('score'), 0)}/{_to_int(q.get('max_score'), 0)}"
            for q in questions
        ]
        parts.append(f"总分 {total}/{sum(_to_int(q.get('max_score'), 0) for q in questions)}")
        score_breakdown = "，".join(parts)

    return {
        "score": total,
        "summary": str(payload.get("summary", "")),
        "comments": str(payload.get("comments", "")),
        "suggestions": str(payload.get("suggestions", "")),
        "score_breakdown": score_breakdown,
        "strengths": payload.get("strengths", []),
        "weaknesses": payload.get("weaknesses", []),
        "next_steps": payload.get("next_steps", []),
        "questions": questions,
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _has_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_text(item) for item in value)
    if isinstance(value, dict):
        return any(_has_text(item) for item in value.values())
    return value not in (None, "")


def _is_deepseek_base_url(base_url: str) -> bool:
    return "deepseek.com" in base_url.lower()


# ---------------------------------------------------------------------------
# 一致性校验（阶段二输出自检）
# ---------------------------------------------------------------------------


def _correction_consistency_issues(payload: dict[str, Any]) -> list[str]:
    """检查批改结果 JSON 内部的逻辑一致性。"""
    issues: list[str] = []
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return issues

    # 总分与逐题得分之和是否一致
    declared_score = _to_int(payload.get("score"), 0)
    per_question_sum = sum(_to_int(q.get("score"), 0) for q in questions if isinstance(q, dict))
    if declared_score != per_question_sum:
        issues.append(
            f"总分 score={declared_score} 与逐题得分之和 {per_question_sum} 不一致，"
            f"请确保逐题 score 加起来等于总分。"
        )

    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            continue

        question_no = item.get("question_no") or index
        is_correct = item.get("is_correct")
        score = _to_float(item.get("score"))
        max_score = _to_float(item.get("max_score"))
        has_deduction = _has_text(item.get("deduction_reason"))
        has_uncertain = _has_text(item.get("uncertain_reason"))
        correct_answer = str(item.get("correct_answer", ""))

        if is_correct is True and (has_deduction or has_uncertain):
            issues.append(f"第 {question_no} 题 is_correct=true，但存在扣分原因或不确定原因。")
        if any(marker in correct_answer for marker in ("之前判断", "需修正", "重新计算", "不对，")):
            issues.append(f"第 {question_no} 题 correct_answer 包含自我纠错文字，不是干净的最终答案。")
        if score is not None and max_score is not None:
            if (has_deduction or has_uncertain) and score >= max_score:
                issues.append(f"第 {question_no} 题存在扣分/不确定原因，但 score 未小于 max_score。")
            if is_correct is False and score >= max_score:
                issues.append(f"第 {question_no} 题 is_correct=false，但得分为满分。")
            if is_correct is True and score < max_score:
                issues.append(f"第 {question_no} 题 is_correct=true，但得分低于满分。")
            if score < max_score and not (has_deduction or has_uncertain):
                issues.append(f"第 {question_no} 题得分低于满分，但 deduction_reason 和 uncertain_reason 均为空。")

    return issues


# ---------------------------------------------------------------------------
# 跨阶段校验：确保阶段二的 correct_answer 与阶段一一致
# ---------------------------------------------------------------------------


def _validate_correct_answers(
    derived_answers: list[dict[str, Any]],
    correction: dict[str, Any],
) -> list[str]:
    """检查批改结果中的 correct_answer 是否与推导阶段一致。

    如果阶段二的 correct_answer 被 LLM 私自改动，说明 LLM 在"纠正"阶段一的答案，
    这很可能是错误的（阶段一专注计算，准确率更高）。
    """
    issues: list[str] = []

    # 按 question_no 建立阶段一答案索引
    derived_map: dict[str, dict[str, Any]] = {}
    for item in derived_answers:
        qno = str(item.get("question_no", ""))
        if qno:
            derived_map[qno] = item

    correction_questions = correction.get("questions")
    if not isinstance(correction_questions, list):
        return issues

    for item in correction_questions:
        if not isinstance(item, dict):
            continue
        qno = str(item.get("question_no", ""))
        derived = derived_map.get(qno)
        if not derived:
            continue

        derived_answer = str(derived.get("correct_answer", "")).strip()
        correction_answer = str(item.get("correct_answer", "")).strip()

        if not derived_answer or not correction_answer:
            continue

        # 做宽松比对：去掉空白和标点差异后比较
        def _normalize(s: str) -> str:
            return re.sub(r"\s+", "", s).replace("，", ",").replace("；", ";")

        if _normalize(derived_answer) != _normalize(correction_answer):
            issues.append(
                f"第 {qno} 题 correct_answer 与推导阶段不一致。"
                f"推导阶段：{derived_answer[:80]}；"
                f"批改阶段：{correction_answer[:80]}。请使用推导阶段的标准答案。"
            )

    return issues


# ---------------------------------------------------------------------------
# LLM 请求基础设施
# ---------------------------------------------------------------------------


def _save_debug_response(label: str, payload: dict[str, Any], response_text: str) -> None:
    """保存原始 LLM 请求和响应用于调试。"""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = DEBUG_DIR / f"{label}_{timestamp}.json"
        # 只保存关键信息，不泄露完整 API key
        safe_payload = {
            "model": payload.get("model"),
            "message_count": len(payload.get("messages", [])),
            "temperature": payload.get("temperature"),
            "max_tokens": payload.get("max_tokens"),
            "user_prompt_preview": (
                payload.get("messages", [{}])[-1].get("content", "")[:500]
                if payload.get("messages")
                else ""
            ),
        }
        path.write_text(
            json.dumps(
                {
                    "label": label,
                    "timestamp": timestamp,
                    "request": safe_payload,
                    "response": response_text[:5000],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass  # 调试日志不阻断主流程


def _make_llm_request(
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    debug_label: str = "llm",
) -> str:
    """发送 LLM 请求并返回原始 content 字符串。"""
    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"大模型接口请求失败：{exc}") from exc

    _raise_for_llm_error(response)
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("大模型返回结构不符合 Chat Completions 格式。") from exc

    _save_debug_response(debug_label, payload, content)
    return content


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


# ---------------------------------------------------------------------------
# 阶段一：推导答案
# ---------------------------------------------------------------------------


def _build_derive_payload(
    *,
    model: str,
    ocr_result: dict[str, Any],
    max_tokens: int,
    retry_json: bool = False,
    deepseek_thinking: str = "disabled",
) -> dict[str, Any]:
    user_prompt = _derive_answers_prompt(ocr_result)
    if retry_json:
        user_prompt = (
            "上一次回答没有形成可解析的 JSON。请重新输出，只返回一个合法 JSON 对象。\n\n"
            + user_prompt
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只返回严格 JSON，不输出 Markdown 或解释文字。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    if deepseek_thinking:
        payload["thinking"] = {"type": deepseek_thinking}

    return payload


def _request_derive_answers(
    *,
    base_url: str,
    api_key: str,
    model: str,
    ocr_result: dict[str, Any],
    max_tokens: int,
    retry_json: bool = False,
) -> list[dict[str, Any]]:
    deepseek_thinking = ""
    if _is_deepseek_base_url(base_url):
        deepseek_thinking = (get_setting("DEEPSEEK_THINKING", "disabled") or "disabled").lower()

    payload = _build_derive_payload(
        model=model,
        ocr_result=ocr_result,
        max_tokens=max_tokens,
        retry_json=retry_json,
        deepseek_thinking=deepseek_thinking,
    )

    content = _make_llm_request(
        base_url=base_url,
        api_key=api_key,
        payload=payload,
        debug_label="derive_answers" + ("_retry" if retry_json else ""),
    )

    parsed = _parse_json_response(content)
    return _parse_derived_answers(parsed)


def _derive_answers_with_retry(
    *,
    base_url: str,
    api_key: str,
    model: str,
    ocr_result: dict[str, Any],
    max_tokens: int,
) -> list[dict[str, Any]]:
    """推导答案，JSON 解析失败时自动重试一次。"""
    try:
        return _request_derive_answers(
            base_url=base_url,
            api_key=api_key,
            model=model,
            ocr_result=ocr_result,
            max_tokens=max_tokens,
        )
    except ValueError:
        return _request_derive_answers(
            base_url=base_url,
            api_key=api_key,
            model=model,
            ocr_result=ocr_result,
            max_tokens=max_tokens,
            retry_json=True,
        )


# ---------------------------------------------------------------------------
# 阶段二：批改评分
# ---------------------------------------------------------------------------


def _build_grade_payload(
    *,
    model: str,
    ocr_result: dict[str, Any],
    derived_answers: list[dict[str, Any]],
    max_tokens: int,
    retry_json: bool = False,
    consistency_issues: list[str] | None = None,
    previous_correction: dict[str, Any] | None = None,
    deepseek_thinking: str = "disabled",
) -> dict[str, Any]:
    if retry_json:
        user_prompt = _grade_json_retry_prompt(ocr_result, derived_answers)
    elif consistency_issues:
        user_prompt = _grade_consistency_retry_prompt(
            ocr_result, derived_answers, consistency_issues, previous_correction
        )
    else:
        user_prompt = _grade_prompt(ocr_result, derived_answers)

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只返回严格 JSON，不输出 Markdown 或解释文字。"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    if deepseek_thinking:
        payload["thinking"] = {"type": deepseek_thinking}

    return payload


def _request_grade(
    *,
    base_url: str,
    api_key: str,
    model: str,
    ocr_result: dict[str, Any],
    derived_answers: list[dict[str, Any]],
    max_tokens: int,
    retry_json: bool = False,
    consistency_issues: list[str] | None = None,
    previous_correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deepseek_thinking = ""
    if _is_deepseek_base_url(base_url):
        deepseek_thinking = (get_setting("DEEPSEEK_THINKING", "disabled") or "disabled").lower()

    payload = _build_grade_payload(
        model=model,
        ocr_result=ocr_result,
        derived_answers=derived_answers,
        max_tokens=max_tokens,
        retry_json=retry_json,
        consistency_issues=consistency_issues,
        previous_correction=previous_correction,
        deepseek_thinking=deepseek_thinking,
    )

    label = "grade"
    if retry_json:
        label = "grade_json_retry"
    elif consistency_issues:
        label = "grade_consistency_retry"

    content = _make_llm_request(
        base_url=base_url,
        api_key=api_key,
        payload=payload,
        debug_label=label,
    )

    return _normalize_correction(_parse_json_response(content))


def _request_grade_with_json_retry(
    *,
    base_url: str,
    api_key: str,
    model: str,
    ocr_result: dict[str, Any],
    derived_answers: list[dict[str, Any]],
    max_tokens: int,
    consistency_issues: list[str] | None = None,
    previous_correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """批改请求，JSON 解析失败时自动重试一次。"""
    try:
        return _request_grade(
            base_url=base_url,
            api_key=api_key,
            model=model,
            ocr_result=ocr_result,
            derived_answers=derived_answers,
            max_tokens=max_tokens,
            consistency_issues=consistency_issues,
            previous_correction=previous_correction,
        )
    except ValueError:
        return _request_grade(
            base_url=base_url,
            api_key=api_key,
            model=model,
            ocr_result=ocr_result,
            derived_answers=derived_answers,
            max_tokens=max_tokens,
            retry_json=True,
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def correct_homework(ocr_result: dict[str, Any]) -> dict[str, Any]:
    """批改作业。

    两阶段流程：
    1. 推导阶段：LLM 专注理解题目、计算标准答案
    2. 批改阶段：LLM 拿到标准答案后，专注比对、打分、写反馈
    两阶段之间会校验 correct_answer 是否一致，不一致则追加重试。
    """
    mode = (get_setting("LLM_MODE", "api") or "api").lower()
    if mode == "mock":
        return _mock_correction(ocr_result)

    api_key = get_setting("LLM_API_KEY") or get_setting("DEEPSEEK_API_KEY")
    base_url = (get_setting("LLM_BASE_URL", DEFAULT_LLM_BASE_URL) or "").rstrip("/")
    model = get_setting("LLM_MODEL", DEFAULT_LLM_MODEL)
    max_tokens = max(256, get_int_setting("LLM_MAX_TOKENS", DEFAULT_MAX_TOKENS))
    consistency_retries = max(0, get_int_setting("LLM_CONSISTENCY_RETRIES", DEFAULT_CONSISTENCY_RETRIES))

    if not api_key:
        raise LLMConfigError(
            "未配置 LLM_API_KEY 或 DEEPSEEK_API_KEY。可以在 .env 或 Streamlit Secrets 中配置，"
            "或将 LLM_MODE 设置为 mock 先跑通演示流程。"
        )

    # ==================== 阶段一：推导正确答案 ====================
    derived_answers = _derive_answers_with_retry(
        base_url=base_url,
        api_key=api_key,
        model=model,
        ocr_result=ocr_result,
        max_tokens=max_tokens,
    )

    if not derived_answers:
        raise RuntimeError("推导答案阶段未能产出任何题目的标准答案。")

    # ==================== 阶段二：批改评分 ====================
    correction = _request_grade_with_json_retry(
        base_url=base_url,
        api_key=api_key,
        model=model,
        ocr_result=ocr_result,
        derived_answers=derived_answers,
        max_tokens=max_tokens,
    )

    # ==================== 重试循环 ====================
    # 每次重试会同时检查：
    #   a) 跨阶段校验：correct_answer 是否与阶段一一致
    #   b) 内部一致性校验：is_correct/score/deduction_reason 是否自洽
    for attempt in range(consistency_retries + 1):  # +1 把首次也算入尝试
        # 跨阶段校验
        answer_issues = _validate_correct_answers(derived_answers, correction)

        # 内部一致性校验
        consistency_issues = _correction_consistency_issues(correction)

        all_issues = answer_issues + consistency_issues

        if not all_issues:
            # 全部通过，返回结果
            return correction

        if attempt >= consistency_retries:
            # 已达最大重试次数，记录但不再重试
            break

        # 带着问题重试
        correction = _request_grade_with_json_retry(
            base_url=base_url,
            api_key=api_key,
            model=model,
            ocr_result=ocr_result,
            derived_answers=derived_answers,
            max_tokens=max_tokens,
            consistency_issues=all_issues,
            previous_correction=correction,
        )

    return correction
