from __future__ import annotations

from typing import Any

from homework_corrector.auth.auth import get_display_names
from homework_corrector.storage.storage import list_results, load_result


STATUS_LABELS = {
    "waiting": "等待中",
    "running": "处理中",
    "finished": "已完成",
    "failed": "失败",
    "unknown": "未知",
}

IMAGE_INPUT_TYPES = ["png", "jpg", "jpeg", "webp", "bmp"]
SUBJECT_CATEGORIES = ("数学", "语文", "英语", "物理", "化学", "生物", "地理", "历史", "政治", "其他")


def _score_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_totals(questions: list[dict[str, Any]]) -> tuple[float, float] | None:
    total_score = 0.0
    total_max_score = 0.0
    has_score = False
    for item in questions:
        if not isinstance(item, dict):
            continue
        score = _score_number(item.get("score"))
        max_score = _score_number(item.get("max_score"))
        if score is None or max_score is None:
            continue
        total_score += score
        total_max_score += max_score
        has_score = True

    if not has_score or total_max_score <= 0:
        return None
    return total_score, total_max_score


def _format_score_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _score_display(questions: list[dict[str, Any]]) -> str:
    totals = _score_totals(questions)
    if totals is None:
        return "-"
    total_score, total_max_score = totals
    return f"{_format_score_number(total_score)}/{_format_score_number(total_max_score)}"


def _correct_rate(questions: list[dict[str, Any]]) -> str:
    totals = _score_totals(questions)
    if totals is None:
        return "-"
    total_score, total_max_score = totals
    return f"{total_score / total_max_score:.0%}"


def _score_rate_value(questions: list[dict[str, Any]]) -> float | None:
    totals = _score_totals(questions)
    if totals is None:
        return None
    total_score, total_max_score = totals
    return total_score / total_max_score


def _result_score_totals(result: dict[str, Any]) -> tuple[float, float] | None:
    score = _score_number(result.get("score"))
    max_score = _score_number(result.get("max_score"))
    if score is not None and max_score is not None and max_score > 0:
        return score, max_score
    return _score_totals(result.get("questions", []))


def _finished_results(owner_username: str | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in list_results(owner_username=owner_username):
        if item.get("status") != "finished":
            continue
        if item.get("questions"):
            results.append(item)
            continue
        task_id = str(item.get("task_id") or "").strip()
        if not task_id:
            continue
        detail = load_result(task_id, owner_username=owner_username)
        if detail and detail.get("status") == "finished" and detail.get("questions"):
            results.append(detail)
    return results


def _result_subject(result: dict[str, Any]) -> str:
    subject = str(result.get("subject") or "").strip()
    return subject if subject in SUBJECT_CATEGORIES else "其他"


def _is_wrong_question(item: dict[str, Any]) -> bool:
    if item.get("is_correct") is False:
        return True
    score = _score_number(item.get("score"))
    max_score = _score_number(item.get("max_score"))
    return score is not None and max_score is not None and max_score > 0 and score < max_score


def _question_key(item: dict[str, Any], fallback_index: int = 1) -> str:
    value = item.get("question_no") or item.get("subject_index") or fallback_index
    return str(value).strip()


def _question_options(questions: list[dict[str, Any]]) -> list[str]:
    return [_question_key(item, index) for index, item in enumerate(questions, start=1) if isinstance(item, dict)]


def _question_by_no(questions: list[dict[str, Any]], question_no: str) -> dict[str, Any]:
    for index, item in enumerate(questions, start=1):
        if isinstance(item, dict) and _question_key(item, index) == str(question_no):
            return item
    return {}


def _paper_cut_question_by_no(paper_cut_questions: list[dict[str, Any]], question_no: str) -> dict[str, Any]:
    return _question_by_no(paper_cut_questions, question_no)


def _wrong_question_records(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result in results:
        subject = _result_subject(result)
        task_id = result.get("task_id", "")
        finished_at = result.get("finished_at") or result.get("saved_at") or ""
        questions = result.get("questions", [])
        paper_cut_questions = [
            item for item in result.get("paper_cut_questions", []) if isinstance(item, dict)
        ]
        for index, item in enumerate(result.get("questions", []), start=1):
            if not isinstance(item, dict) or not _is_wrong_question(item):
                continue
            question_no = _question_key(item, index)
            paper_cut_question = _paper_cut_question_by_no(paper_cut_questions, question_no)
            records.append(
                {
                    **item,
                    "_subject": subject,
                    "_task_id": task_id,
                    "_finished_at": finished_at,
                    "_question_no": question_no,
                    "_task_score": _score_display(questions),
                    "_crop_path": paper_cut_question.get("crop_path") or "",
                }
            )
    return records


def _wrong_question_groups_by_subject(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {subject: [] for subject in SUBJECT_CATEGORIES}
    for result in sorted(
        results,
        key=lambda item: item.get("finished_at") or item.get("saved_at") or "",
        reverse=True,
    ):
        records = _wrong_question_records([result])
        if not records:
            continue
        subject = _result_subject(result)
        groups.setdefault(subject, []).append(
            {
                "subject": subject,
                "task_id": result.get("task_id", ""),
                "finished_at": result.get("finished_at") or result.get("saved_at") or "",
                "score": _score_display(result.get("questions", [])),
                "wrong_count": len(records),
                "records": records,
            }
        )
    return groups


def _leaderboard_rows(limit: int = 5) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    by_user: dict[str, dict[str, Any]] = {}
    for result in list_results():
        if result.get("status") != "finished":
            continue
        username = str(result.get("owner_username") or "").strip().lower()
        totals = _result_score_totals(result)
        if not username or totals is None:
            continue
        score, max_score = totals
        by_user.setdefault(username, {"rates": [], "total_score": 0.0})
        by_user[username]["rates"].append(score / max_score)
        by_user[username]["total_score"] += score

    display_names = get_display_names(set(by_user))
    rows = [
        {
            "username": username,
            "display_name": (display_names.get(username) or username),
            "average_rate": sum(data["rates"]) / len(data["rates"]),
            "average_rate_label": f"{sum(data['rates']) / len(data['rates']):.0%}",
            "task_count": len(data["rates"]),
            "total_score": data["total_score"],
            "total_score_label": _format_score_number(data["total_score"]),
        }
        for username, data in by_user.items()
        if data["rates"]
    ]
    rows.sort(key=lambda item: (-item["average_rate"], -item["task_count"], item["display_name"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows[:limit], {row["username"]: row for row in rows}
