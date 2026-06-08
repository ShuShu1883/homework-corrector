from __future__ import annotations

from typing import Any

import streamlit as st

from resource_paths import display_resource, resource_exists
from score_utils import (
    STATUS_LABELS,
    _correct_rate,
    _paper_cut_question_by_no,
    _question_by_no,
    _question_options,
    _result_subject,
    _score_display,
)
from storage import list_results, load_result
from task_queue import get_task_status, list_tasks
from ui_theme import build_task_card_html, render_page_intro, task_card_button_key


def _stringify_detail(value: Any, default: str = "暂无") -> str:
    if value in (None, ""):
        return default
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "\n".join(f"- {item}" for item in parts) if parts else default
    if isinstance(value, dict):
        parts = [f"{key}：{val}" for key, val in value.items() if val not in (None, "", [])]
        return "\n".join(f"- {item}" for item in parts) if parts else default
    return str(value)


def _write_detail(label: str, value: Any) -> None:
    st.markdown(f"**{label}**")
    text = _stringify_detail(value)
    if text.startswith("- "):
        st.markdown(text)
    else:
        st.write(text)


def _question_display_status(item: dict[str, Any]) -> str:
    is_correct = item.get("is_correct")
    if is_correct is True:
        return "正确"
    if is_correct is False:
        return "错误"
    return "待判断"


def _result_error_message(status: dict[str, Any], result: dict[str, Any] | None) -> str | None:
    if result and result.get("status") == "failed":
        return result.get("error") or status.get("error") or "任务处理失败。"
    return status.get("error")


def _write_overview(result: dict[str, Any]) -> None:
    st.write(result.get("summary") or "暂无总体评价。")
    if result.get("score_breakdown"):
        st.markdown("**评分构成**")
        st.write(_stringify_detail(result.get("score_breakdown")))
    st.markdown("**总体批注**")
    st.write(result.get("comments") or "暂无批注。")
    st.markdown("**学习建议**")
    st.write(result.get("suggestions") or "暂无建议。")
    extra_cols = st.columns(3)
    with extra_cols[0]:
        _write_detail("优势", result.get("strengths"))
    with extra_cols[1]:
        _write_detail("薄弱点", result.get("weaknesses"))
    with extra_cols[2]:
        _write_detail("下一步", result.get("next_steps"))


def _write_question_detail(item: dict[str, Any], paper_cut_question: dict[str, Any]) -> None:
    badge = _question_display_status(item)
    st.markdown(f"#### 第 {item.get('question_no', '-')} 题 · {badge}")
    cols = st.columns(2)
    cols[0].metric("本题得分", item.get("score", "-"))
    cols[1].metric("本题满分", item.get("max_score", "-"))

    crop_path = paper_cut_question.get("crop_path")
    if crop_path and resource_exists(crop_path):
        with st.expander("查看本题区域", expanded=False):
            st.image(display_resource(crop_path), width="stretch")

    _write_detail("题目理解", item.get("question_understanding"))
    st.write(f"学生答案：{item.get('student_answer', '-')}")
    st.write(f"正确答案：{item.get('correct_answer', '-')}")
    _write_detail("详细题解", item.get("solution_steps") or item.get("analysis"))
    _write_detail("错因分析", item.get("mistake_analysis"))
    _write_detail("扣分原因", item.get("deduction_reason"))
    _write_detail("订正建议", item.get("revision_advice"))
    _write_detail("相关知识点", item.get("knowledge_points"))
    st.write(f"批注：{item.get('comment', '-')}")
    if item.get("uncertain_reason"):
        st.warning(f"OCR 不确定说明：{item.get('uncertain_reason')}")


def _build_report(result: dict[str, Any]) -> str:
    lines = [
        "# 智能作业批改报告",
        "",
        f"- 任务ID：{result.get('task_id', '-')}",
        f"- 状态：{STATUS_LABELS.get(result.get('status'), result.get('status', '-'))}",
        f"- 科目：{_result_subject(result)}",
        f"- 总分：{_score_display(result.get('questions', []))}",
        f"- 正确率：{_correct_rate(result.get('questions', []))}",
        f"- 识别题数：{len(result.get('paper_cut_questions') or result.get('questions', []))}",
        f"- 批注图：{result.get('annotated_image_path') or '暂无'}",
        "",
        "## 总体评价",
        result.get("summary") or "暂无",
        "",
        "## 评分构成",
        _stringify_detail(result.get("score_breakdown")),
        "",
        "## 批注",
        result.get("comments") or "暂无",
        "",
        "## 学习建议",
        result.get("suggestions") or "暂无",
        "",
        "## 优势",
        _stringify_detail(result.get("strengths")),
        "",
        "## 薄弱点",
        _stringify_detail(result.get("weaknesses")),
        "",
        "## 下一步",
        _stringify_detail(result.get("next_steps")),
        "",
        "## OCR 识别结果",
        "```text",
        result.get("ocr_text") or "",
        "```",
        "",
        "## 逐题结果",
    ]

    for item in result.get("questions", []):
        status = _question_display_status(item)
        lines.extend(
            [
                "",
                f"### 第 {item.get('question_no', '-')} 题：{status}",
                f"- 得分：{item.get('score', '-')} / {item.get('max_score', '-')}",
                f"- 题目理解：{item.get('question_understanding', '-')}",
                f"- 学生答案：{item.get('student_answer', '-')}",
                f"- 正确答案：{item.get('correct_answer', '-')}",
                f"- 分析：{item.get('analysis', '-')}",
                f"- 批注：{item.get('comment', '-')}",
                f"- 扣分原因：{item.get('deduction_reason', '-')}",
                "",
                "#### 详细题解",
                _stringify_detail(item.get("solution_steps")),
                "",
                "#### 错因分析",
                _stringify_detail(item.get("mistake_analysis")),
                "",
                "#### 订正建议",
                _stringify_detail(item.get("revision_advice")),
                "",
                "#### 知识点",
                _stringify_detail(item.get("knowledge_points")),
            ]
        )
        if item.get("uncertain_reason"):
            lines.extend(["", f"- OCR 不确定说明：{item.get('uncertain_reason')}"])
    return "\n".join(lines)


def _task_rows(owner_username: str) -> list[dict[str, Any]]:
    rows = []
    known_ids = set()
    for item in list_tasks(owner_username=owner_username):
        known_ids.add(item["task_id"])
        result = load_result(item["task_id"], owner_username=owner_username)
        rows.append(
            {
                "任务ID": item["task_id"],
                "状态": STATUS_LABELS.get(item.get("status"), item.get("status")),
                "_status": item.get("status"),
                "分数": _score_display(result.get("questions", [])) if result else "-",
                "创建时间": item.get("created_at", "-"),
                "更新时间": item.get("updated_at", "-"),
            }
        )

    for result in list_results(owner_username=owner_username):
        task_id = result.get("task_id")
        if not task_id or task_id in known_ids:
            continue
        rows.append(
            {
                "任务ID": task_id,
                "状态": STATUS_LABELS.get(result.get("status"), result.get("status")),
                "_status": result.get("status"),
                "分数": _score_display(result.get("questions", [])),
                "创建时间": result.get("saved_at", "-"),
                "更新时间": result.get("finished_at", result.get("saved_at", "-")),
            }
        )
    return rows


def _show_result(task_id: str, owner_username: str) -> None:
    status = get_task_status(task_id, owner_username=owner_username)
    result = load_result(task_id, owner_username=owner_username)

    if status.get("status") == "unknown" and not result:
        st.error("任务不存在，或你无权查看该任务。")
        return

    render_page_intro("任务详情", "查看批改进度、整体评价与逐题反馈。", kicker="Correction report ✦")
    status_text = STATUS_LABELS.get(status.get("status"), status.get("status", "未知"))
    st.caption(f"任务ID：{task_id}")

    question_count = len(result.get("paper_cut_questions") or result.get("questions", [])) if result else "-"
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("状态", status_text)
    col_b.metric("总分", _score_display(result.get("questions", [])) if result else "-")
    col_c.metric("正确率", _correct_rate(result.get("questions", [])) if result else "-")
    col_d.metric("识别题数", question_count)

    error_message = _result_error_message(status, result)
    image_path = (result.get("image_path") if result else None) or status.get("image_path")
    if not result:
        if error_message:
            st.error(error_message)
        st.info("任务尚未完成。点击刷新可查看最新状态。")
        return

    if result.get("status") == "failed":
        st.error(error_message or "任务处理失败。")
        return

    if error_message:
        st.error(error_message)

    with st.expander("查看整体评价", expanded=False):
        _write_overview(result)

    if result.get("status") == "finished":
        questions = [item for item in result.get("questions", []) if isinstance(item, dict)]
        paper_cut_questions = [
            item for item in result.get("paper_cut_questions", []) if isinstance(item, dict)
        ]
        question_options = _question_options(questions)

        left, right = st.columns([1.12, 1], gap="large")
        with left:
            st.markdown("#### 批注结果图")
            annotated_image_path = result.get("annotated_image_path")
            if annotated_image_path and resource_exists(annotated_image_path):
                st.image(display_resource(annotated_image_path), width="stretch")
            else:
                st.info("暂无可显示的批注图。")

        with right:
            st.markdown("#### 单题批改")
            if not question_options:
                st.info("暂无逐题批改结果。")
            else:
                selected_question_no = st.segmented_control(
                    "选择题目",
                    question_options,
                    default=question_options[0],
                    format_func=lambda value: f"题目 {value}",
                    key=f"selected_question_no_{task_id}",
                    label_visibility="collapsed",
                    width="stretch",
                )
                selected_question_no = str(selected_question_no or question_options[0])
                selected_question = _question_by_no(questions, selected_question_no)
                selected_paper_cut_question = _paper_cut_question_by_no(
                    paper_cut_questions,
                    selected_question_no,
                )
                if selected_question:
                    _write_question_detail(selected_question, selected_paper_cut_question)
                else:
                    st.info("未找到对应题目的批改详情。")

    with st.expander("查看原始图片与 OCR 文本", expanded=False):
        st.markdown("#### 原始作业图")
        image_preview_path = result.get("image_preview_path") or image_path
        if image_preview_path and resource_exists(image_preview_path):
            st.image(display_resource(image_preview_path), width="stretch")
        else:
            st.info("暂无可显示的原图。")

        ocr_image_path = result.get("ocr_image_path")
        ocr_preview_path = result.get("ocr_preview_path") or ocr_image_path
        if ocr_preview_path and resource_exists(ocr_preview_path):
            st.markdown("#### 增强后识别图")
            st.image(display_resource(ocr_preview_path), width="stretch")

        st.markdown("#### OCR 识别文本")
        st.text_area("OCR 文本", result.get("ocr_text", ""), height=160, label_visibility="collapsed")

    report = _build_report(result)
    st.download_button(
        "下载 Markdown 报告",
        data=report.encode("utf-8"),
        file_name=f"homework_report_{task_id}.md",
        mime="text/markdown",
    )


def show_records_page(owner_username: str) -> None:
    render_page_intro("批改记录", "集中查看自己的历史批改记录和当前处理进度。", kicker="My homework archive ✦")
    rows = _task_rows(owner_username)
    if not rows:
        st.info("暂无批改记录。")
        return

    summary_cols = st.columns(3)
    summary_cols[0].metric("任务总数", len(rows))
    summary_cols[1].metric("已完成", sum(row.get("_status") == "finished" for row in rows))
    summary_cols[2].metric(
        "处理中",
        sum(row.get("_status") in {"waiting", "running"} for row in rows),
    )

    st.markdown("#### 批改记录")
    for row in rows:
        task_id = str(row["任务ID"])
        with st.container(border=True):
            st.markdown(build_task_card_html(row), unsafe_allow_html=True)
            if st.button("查看详情", key=task_card_button_key(task_id), width="stretch"):
                st.session_state["selected_task_id"] = task_id

    selected_task_id = st.session_state.get("selected_task_id")
    if selected_task_id:
        st.divider()
        _show_result(str(selected_task_id), owner_username)
