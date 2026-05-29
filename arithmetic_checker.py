from __future__ import annotations

import ast
import re
from fractions import Fraction
from typing import Any


_MATH_TRANSLATION = str.maketrans(
    {
        "（": "(",
        "）": ")",
        "＋": "+",
        "－": "-",
        "−": "-",
        "—": "-",
        "＝": "=",
        "，": "",
        ",": "",
        " ": "",
    }
)


def _question_key(item: dict[str, Any], fallback_index: int) -> str:
    raw = item.get("question_no") or item.get("subject_index") or fallback_index
    return str(raw).strip()


def _clean_line(line: str) -> str:
    if any(marker in line for marker in ("\\cdots", "\\ldots", "...", "…", "余")):
        return ""
    text = line.strip()
    text = re.sub(r"^\s*\d+\s*[、.．]\s*", "", text)
    text = text.replace("\\times", "*").replace("\\div", "/")
    text = text.replace("×", "*").replace("÷", "/")
    text = text.replace("$", "").replace("\\(", "").replace("\\)", "")
    text = text.translate(_MATH_TRANSLATION)
    text = re.sub(r"[^0-9+\-*/().=]", "", text)
    text = re.sub(r"(?<=[=+\-*/(])\.", "0.", text)
    return text.strip()


def _split_math_lines(text: str) -> list[str]:
    normalized = str(text or "").replace("\\n", "\n")
    lines: list[str] = []
    for raw in normalized.splitlines():
        cleaned = _clean_line(raw)
        if cleaned and any(char.isdigit() for char in cleaned) and any(op in cleaned for op in "+-*/="):
            lines.append(cleaned)
    return lines


def _has_operator(expression: str) -> bool:
    return any(op in expression for op in "+-*/")


def _extract_chains(text: str) -> list[list[str]]:
    chains: list[list[str]] = []
    current: list[str] = []
    seen: set[tuple[str, ...]] = set()

    def add_chain(chain: list[str]) -> None:
        cleaned = [part for part in chain if part and any(ch.isdigit() for ch in part)]
        if len(cleaned) < 2:
            return
        key = tuple(cleaned)
        if key in seen:
            return
        seen.add(key)
        chains.append(cleaned)

    for line in _split_math_lines(text):
        if line.startswith("="):
            expression = line.lstrip("=")
            if current and expression:
                current.append(expression)
            continue

        if "=" in line:
            add_chain([part for part in line.split("=") if part])
            continue

        if _has_operator(line):
            add_chain(current)
            current = [line]

    add_chain(current)
    return chains


def _number_from_constant(value: Any) -> Fraction:
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    raise ValueError("unsupported number")


def _eval_node(node: ast.AST) -> Fraction:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        return _number_from_constant(node.value)
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("division by zero")
            return left / right
    raise ValueError("unsupported expression")


def _safe_eval(expression: str) -> Fraction | None:
    if not expression or not re.fullmatch(r"[0-9+\-*/().]+", expression):
        return None
    try:
        tree = ast.parse(expression, mode="eval")
        return _eval_node(tree)
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None


def _format_number(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    as_float = value.numerator / value.denominator
    text = f"{as_float:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _display_expression(expression: str) -> str:
    return expression.replace("*", "×").replace("/", "÷")


def _analyze_chain(chain: list[str]) -> dict[str, Any] | None:
    values = [_safe_eval(part) for part in chain]
    if any(value is None for value in values):
        return None

    expected = values[0]
    final = values[-1]
    if expected is None or final is None:
        return None

    wrong_steps = []
    for expression, value in zip(chain[1:], values[1:]):
        if value != expected:
            wrong_steps.append(
                {
                    "expression": _display_expression(expression),
                    "value": _format_number(value),
                    "expected": _format_number(expected),
                }
            )

    is_correct = final == expected and not wrong_steps
    first_expression = _display_expression(chain[0])
    student_expression = _display_expression(chain[-1])
    expected_text = _format_number(expected)
    student_text = _format_number(final)

    if is_correct:
        detail = f"{first_expression}={student_text} 正确。"
    else:
        detail = f"{first_expression} 的正确结果是 {expected_text}，学生最终写成 {student_text}。"

    return {
        "expression": first_expression,
        "student_expression": student_expression,
        "expected": expected_text,
        "student": student_text,
        "is_correct": is_correct,
        "wrong_steps": wrong_steps,
        "detail": detail,
    }


def check_arithmetic_questions(questions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    for index, question in enumerate(questions or [], start=1):
        if not isinstance(question, dict):
            continue
        facts = []
        for chain in _extract_chains(str(question.get("text") or "")):
            fact = _analyze_chain(chain)
            if fact:
                facts.append(fact)
        if not facts:
            continue

        errors = [fact for fact in facts if not fact["is_correct"]]
        key = _question_key(question, index)
        checks[key] = {
            "question_no": key,
            "facts": facts,
            "errors": errors,
            "correct_count": len(facts) - len(errors),
            "error_count": len(errors),
            "total_count": len(facts),
            "has_errors": bool(errors),
        }
    return checks


def arithmetic_checks_to_prompt(checks: dict[str, dict[str, Any]]) -> str:
    lines = []
    for key in sorted(checks.keys(), key=lambda value: (not value.isdigit(), value)):
        check = checks[key]
        if not check.get("facts"):
            continue
        lines.append(f"第 {key} 题：")
        for fact in check["facts"]:
            status = "正确" if fact["is_correct"] else "错误"
            lines.append(f"- {status}：{fact['detail']}")
    return "\n".join(lines)


def arithmetic_error_note(check: dict[str, Any]) -> str:
    details = [fact["detail"] for fact in check.get("errors", [])]
    return "本地基础算术校验：" + "；".join(details)


def score_cap_ratio(check: dict[str, Any]) -> float:
    total = int(check.get("total_count") or 0)
    errors = int(check.get("error_count") or 0)
    if total <= 0 or errors <= 0:
        return 1.0
    correct = max(0, total - errors)
    if correct == 0:
        return 0.5
    return max(0.0, min(1.0, correct / total))
