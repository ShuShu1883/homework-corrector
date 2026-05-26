from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from config import ensure_runtime_dirs
from storage import list_results, load_result
from task_queue import get_task_status, list_tasks, start_workers, submit_task


STATUS_LABELS = {
    "waiting": "等待中",
    "running": "处理中",
    "finished": "已完成",
    "failed": "失败",
    "unknown": "未知",
}


def _correct_rate(questions: list[dict[str, Any]]) -> str:
    if not questions:
        return "-"
    judged = [item for item in questions if isinstance(item.get("is_correct"), bool)]
    if not judged:
        return "-"
    correct = sum(1 for item in judged if item.get("is_correct") is True)
    return f"{correct / len(judged):.0%}"


def _build_report(result: dict[str, Any]) -> str:
    lines = [
        "# 智能作业批改报告",
        "",
        f"- 任务ID：{result.get('task_id', '-')}",
        f"- 状态：{STATUS_LABELS.get(result.get('status'), result.get('status', '-'))}",
        f"- 总分：{result.get('score', '-')}",
        f"- 正确率：{_correct_rate(result.get('questions', []))}",
        "",
        "## 总体评价",
        result.get("summary") or "暂无",
        "",
        "## 批注",
        result.get("comments") or "暂无",
        "",
        "## 学习建议",
        result.get("suggestions") or "暂无",
        "",
        "## OCR 识别结果",
        "```text",
        result.get("ocr_text") or "",
        "```",
        "",
        "## 逐题结果",
    ]

    for item in result.get("questions", []):
        is_correct = item.get("is_correct")
        status = "正确" if is_correct is True else "错误" if is_correct is False else "待判断"
        lines.extend(
            [
                "",
                f"### 第 {item.get('question_no', '-')} 题：{status}",
                f"- 学生答案：{item.get('student_answer', '-')}",
                f"- 正确答案：{item.get('correct_answer', '-')}",
                f"- 分析：{item.get('analysis', '-')}",
                f"- 批注：{item.get('comment', '-')}",
            ]
        )
    return "\n".join(lines)


def _task_rows() -> list[dict[str, Any]]:
    rows = []
    known_ids = set()
    for item in list_tasks():
        known_ids.add(item["task_id"])
        rows.append(
            {
                "任务ID": item["task_id"],
                "状态": STATUS_LABELS.get(item.get("status"), item.get("status")),
                "分数": item.get("score", "-"),
                "创建时间": item.get("created_at", "-"),
                "更新时间": item.get("updated_at", "-"),
            }
        )

    for result in list_results():
        task_id = result.get("task_id")
        if not task_id or task_id in known_ids:
            continue
        rows.append(
            {
                "任务ID": task_id,
                "状态": STATUS_LABELS.get(result.get("status"), result.get("status")),
                "分数": result.get("score", "-"),
                "创建时间": result.get("saved_at", "-"),
                "更新时间": result.get("finished_at", result.get("saved_at", "-")),
            }
        )
    return rows


def _show_result(task_id: str) -> None:
    status = get_task_status(task_id)
    result = load_result(task_id)

    st.subheader("任务详情")
    status_text = STATUS_LABELS.get(status.get("status"), status.get("status", "未知"))
    st.caption(f"任务ID：{task_id}")

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("状态", status_text)
    col_b.metric("总分", result.get("score", "-") if result else status.get("score", "-"))
    col_c.metric("正确率", _correct_rate(result.get("questions", [])) if result else "-")

    if status.get("error"):
        st.error(status["error"])

    image_path = None
    if result:
        image_path = result.get("image_path")
    image_path = image_path or status.get("image_path")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("#### 原始作业图")
        if image_path and Path(image_path).exists():
            st.image(image_path, use_container_width=True)
        else:
            st.info("暂无可显示的原图。")

    with right:
        st.markdown("#### 批改结果")
        if not result:
            st.info("任务尚未完成。点击刷新可查看最新状态。")
            return

        if result.get("status") == "failed":
            st.error(result.get("error") or "任务处理失败。")
            return

        st.write(result.get("summary") or "暂无总体评价。")
        st.markdown("**总体批注**")
        st.write(result.get("comments") or "暂无批注。")
        st.markdown("**学习建议**")
        st.write(result.get("suggestions") or "暂无建议。")

    if result and result.get("status") == "finished":
        st.markdown("#### 逐题批改")
        for item in result.get("questions", []):
            is_correct = item.get("is_correct")
            badge = "正确" if is_correct is True else "错误" if is_correct is False else "待判断"
            with st.expander(f"第 {item.get('question_no', '-')} 题 · {badge}", expanded=False):
                st.write(f"学生答案：{item.get('student_answer', '-')}")
                st.write(f"正确答案：{item.get('correct_answer', '-')}")
                st.write(f"分析：{item.get('analysis', '-')}")
                st.write(f"批注：{item.get('comment', '-')}")

        st.markdown("#### OCR 识别文本")
        st.text_area("OCR 文本", result.get("ocr_text", ""), height=160, label_visibility="collapsed")

        report = _build_report(result)
        st.download_button(
            "下载 Markdown 报告",
            data=report.encode("utf-8"),
            file_name=f"homework_report_{task_id}.md",
            mime="text/markdown",
        )


def main() -> None:
    st.set_page_config(page_title="智能作业批改系统", layout="wide")
    ensure_runtime_dirs()
    start_workers()

    st.title("中小学作业智能批改系统")
    st.caption("Streamlit + 任务队列 + 阿里云 OCR + 大模型批改")

    with st.sidebar:
        st.header("导航")
        page = st.radio("页面", ["上传批改", "任务列表", "项目说明"], label_visibility="collapsed")
        if st.button("刷新状态", use_container_width=True):
            st.rerun()

    if page == "上传批改":
        uploaded_file = st.file_uploader("上传作业图片", type=["png", "jpg", "jpeg", "webp", "bmp"])
        if uploaded_file:
            st.image(uploaded_file, caption="待批改作业", use_container_width=True)

        if st.button("提交批改任务", type="primary", disabled=uploaded_file is None):
            task_id = submit_task(uploaded_file)
            st.session_state["selected_task_id"] = task_id
            st.success(f"任务已提交：{task_id}")

        selected_task_id = st.session_state.get("selected_task_id")
        if selected_task_id:
            st.divider()
            _show_result(selected_task_id)

    elif page == "任务列表":
        rows = _task_rows()
        if not rows:
            st.info("暂无任务。")
            return

        st.dataframe(rows, use_container_width=True, hide_index=True)
        task_ids = [row["任务ID"] for row in rows]
        selected = st.selectbox("选择任务查看详情", task_ids)
        if selected:
            _show_result(selected)

    else:
        st.markdown(
            """
### 项目架构

本系统采用生产者-消费者模型。前端负责提交作业批改任务，内存队列负责缓存请求，后台线程池负责并发执行 OCR 识别和大模型批改，结果以 JSON 文件保存。

### 适用范围

当前版本适合课程设计、本地演示和小规模部署。生产环境可升级为 Redis/Celery、数据库和对象存储，以支持多进程、多节点和更高并发。

### 演示配置

没有真实 API 密钥时，可在 `.env` 中设置：

```text
OCR_MODE=mock
LLM_MODE=mock
```
"""
        )


if __name__ == "__main__":
    main()
