from __future__ import annotations

from typing import Any

import streamlit as st

from resource_paths import display_resource, resource_exists
from result_views import _write_detail
from score_utils import (
    SUBJECT_CATEGORIES,
    _finished_results,
    _leaderboard_rows,
    _score_rate_value,
    _score_display,
    _result_subject,
    _wrong_question_groups_by_subject,
    _wrong_question_records,
)
from ui_theme import build_leaderboard_card_html, render_page_intro


def _show_score_trend_tab(owner_username: str) -> None:
    results = _finished_results(owner_username)
    entries = []
    for result in sorted(results, key=lambda item: item.get("finished_at") or item.get("saved_at") or ""):
        rate = _score_rate_value(result.get("questions", []))
        if rate is None:
            continue
        entries.append(
            {
                "task_id": result.get("task_id", ""),
                "date": (result.get("finished_at") or result.get("saved_at") or "")[:10],
                "rate": rate,
                "score": _score_display(result.get("questions", [])),
                "subject": _result_subject(result),
            }
        )

    if not entries:
        st.info("暂无可用于趋势分析的已完成批改记录。")
        return

    latest = entries[-1]["rate"]
    average = sum(item["rate"] for item in entries) / len(entries)
    best = max(item["rate"] for item in entries)
    cols = st.columns(4)
    cols[0].metric("完成次数", len(entries))
    cols[1].metric("最新得分率", f"{latest:.0%}")
    cols[2].metric("平均得分率", f"{average:.0%}")
    cols[3].metric("最高得分率", f"{best:.0%}")

    import pandas as pd

    chart_df = pd.DataFrame(
        {
            "序号": list(range(1, len(entries) + 1)),
            "得分率": [round(item["rate"] * 100, 2) for item in entries],
        }
    )
    st.markdown("#### 得分率趋势")
    st.line_chart(chart_df.set_index("序号")["得分率"], height=320)

    st.markdown("#### 历次记录")
    table_df = pd.DataFrame(
        [
            {
                "序号": index,
                "日期": item["date"] or "-",
                "科目": item["subject"],
                "分数": item["score"],
                "得分率": f"{item['rate']:.0%}",
                "任务ID": str(item["task_id"])[:8],
            }
            for index, item in enumerate(entries, start=1)
        ]
    )
    st.dataframe(table_df, hide_index=True, width="stretch")


def _show_error_book_tab(owner_username: str) -> None:
    finished_results = _finished_results(owner_username)
    records = _wrong_question_records(finished_results)
    groups_by_subject = _wrong_question_groups_by_subject(finished_results)

    cols = st.columns(3)
    cols[0].metric("错题总数", len(records))
    cols[1].metric("涉及科目", sum(1 for groups in groups_by_subject.values() if groups))
    cols[2].metric("批改记录", len(finished_results))

    selected_subject = st.segmented_control(
        "科目",
        SUBJECT_CATEGORIES,
        default=SUBJECT_CATEGORIES[0],
        key="error_book_subject",
        width="stretch",
    )
    selected_subject = str(selected_subject or SUBJECT_CATEGORIES[0])
    st.markdown(f"### {selected_subject}")

    subject_groups = groups_by_subject.get(selected_subject, [])
    if not subject_groups:
        st.caption("暂无错题")
        return

    for group in subject_groups:
        finished_at = str(group.get("finished_at") or "")[:19] or "-"
        task_id = str(group.get("task_id") or "")
        task_label = task_id[:8] or "-"
        title = (
            f"{finished_at} · 任务 {task_label} · "
            f"{group.get('score', '-')} · {group.get('wrong_count', 0)} 道错题"
        )
        with st.expander(title, expanded=True):
            for record in group.get("records", []):
                _write_error_book_record(record)


def _write_error_book_record(record: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(f"**第 {record.get('_question_no', '-')} 题**")
        image_col, detail_col = st.columns([1, 2])
        crop_path = record.get("_crop_path")
        with image_col:
            if crop_path and resource_exists(crop_path):
                st.image(display_resource(crop_path), caption="题目图片", width="stretch")
            else:
                st.info("暂无题目图片")
        with detail_col:
            cols = st.columns(2)
            cols[0].metric("得分", record.get("score", "-"))
            cols[1].metric("满分", record.get("max_score", "-"))
            st.write(f"学生答案：{record.get('student_answer', '-')}")
            st.write(f"正确答案：{record.get('correct_answer', '-')}")
            _write_detail("错因分析", record.get("mistake_analysis") or record.get("analysis"))
            _write_detail("订正建议", record.get("revision_advice"))
            _write_detail("知识点", record.get("knowledge_points"))


def _show_learning_analysis_page(owner_username: str) -> None:
    render_page_intro("学习分析", "查看得分率变化，并按科目复盘历史错题。", kicker="Learning analysis")
    trend_tab, error_book_tab = st.tabs(["成绩趋势", "错题本"])
    with trend_tab:
        _show_score_trend_tab(owner_username)
    with error_book_tab:
        _show_error_book_tab(owner_username)


def _show_leaderboard_page(owner_username: str) -> None:
    render_page_intro("学习排行榜", "按平均得分率展示前 5 名，并显示你的当前位置。", kicker="Leaderboard")
    top_rows, all_rows = _leaderboard_rows(limit=5)
    if not top_rows:
        st.info("暂无可排行的已完成批改记录。")
        return

    st.markdown("#### Top 5")
    for row in top_rows:
        is_me = row["username"] == owner_username
        st.markdown(build_leaderboard_card_html(row, is_current_user=is_me), unsafe_allow_html=True)

    current_row = all_rows.get(owner_username)
    if current_row and current_row["rank"] > len(top_rows):
        st.divider()
        st.markdown("#### 我的排名")
        st.markdown(build_leaderboard_card_html(current_row, is_current_user=True), unsafe_allow_html=True)
    elif not current_row:
        st.info("你还没有可参与排行的已完成批改记录。")
