from __future__ import annotations

import uuid
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps
import streamlit as st

from config import UPLOAD_DIR, ensure_runtime_dirs
from image_processing import create_preview_image, process_document_image
from paper_cut_tencent import recognize_question_split
from runtime_cleanup import cleanup_runtime_files, clear_runtime_files
from storage import list_results, load_result
from task_queue import get_task_status, list_tasks, start_workers, submit_task


STATUS_LABELS = {
    "waiting": "等待中",
    "running": "处理中",
    "finished": "已完成",
    "failed": "失败",
    "unknown": "未知",
}


def _uploaded_preview(uploaded_file: Any, max_side: int = 1100) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(BytesIO(bytes(uploaded_file.getbuffer())))).convert("RGB")
    longest = max(image.size)
    if longest > max_side:
        scale = max_side / longest
        image = image.resize(
            (max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def _uploaded_file_signature(uploaded_file: Any) -> str:
    data = bytes(uploaded_file.getbuffer())
    digest = hashlib.blake2b(data, digest_size=8).hexdigest()
    return f"{uploaded_file.name}:{uploaded_file.size}:{digest}"


def _correct_rate(questions: list[dict[str, Any]]) -> str:
    if not questions:
        return "-"
    judged = [item for item in questions if isinstance(item.get("is_correct"), bool)]
    if not judged:
        return "-"
    correct = sum(1 for item in judged if item.get("is_correct") is True)
    return f"{correct / len(judged):.0%}"


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


def _build_report(result: dict[str, Any]) -> str:
    lines = [
        "# 智能作业批改报告",
        "",
        f"- 任务ID：{result.get('task_id', '-')}",
        f"- 状态：{STATUS_LABELS.get(result.get('status'), result.get('status', '-'))}",
        f"- 总分：{result.get('score', '-')}",
        f"- 正确率：{_correct_rate(result.get('questions', []))}",
        f"- 识别题数：{len(result.get('paper_cut_questions') or result.get('questions', []))}",
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
        is_correct = item.get("is_correct")
        status = "正确" if is_correct is True else "错误" if is_correct is False else "待判断"
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


def _save_processing_upload(uploaded_file: Any) -> str:
    ensure_runtime_dirs()
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        suffix = ".png"

    image_path = UPLOAD_DIR / f"processing_{uuid.uuid4()}{suffix}"
    image_path.write_bytes(bytes(uploaded_file.getbuffer()))
    return str(image_path)


def _show_image_processing_page() -> None:
    st.subheader("图片加工")
    st.caption("自动检测文档边界，裁边拉正，并增强文字效果。")

    uploaded_file = st.file_uploader(
        "上传需要加工的作业图片",
        type=["png", "jpg", "jpeg", "webp", "bmp"],
        key="processing_uploader",
    )
    if not uploaded_file:
        st.info("请先上传一张整页作业照片或扫描图片。")
        return

    mode_labels = {
        "strong": "强力清晰",
        "standard": "标准清晰",
        "soft": "自然清晰",
    }
    selected_mode = st.segmented_control(
        "增强模式",
        options=list(mode_labels.keys()),
        format_func=lambda item: mode_labels[item],
        default="strong",
    )

    original_path = _save_processing_upload(uploaded_file)
    result = process_document_image(original_path, enhance_mode=selected_mode or "strong")
    original_preview_path = create_preview_image(original_path, suffix="original_preview")
    warped_preview_path = (
        create_preview_image(result["warped_path"], suffix="warped_preview")
        if result.get("warped_path")
        else None
    )
    enhanced_preview_path = (
        create_preview_image(result["enhanced_path"], suffix="enhanced_preview")
        if result.get("enhanced_path")
        else None
    )

    status = result.get("status")
    if status == "success":
        st.success(result.get("message"))
    elif status == "fallback":
        st.warning(result.get("message"))
    else:
        st.error(result.get("message") or "图片处理失败。")
        st.image(original_path, caption="原图", use_container_width=True)
        return

    columns = st.columns(3)
    with columns[0]:
        st.markdown("#### 原图")
        st.image(original_preview_path, use_container_width=True)

    with columns[1]:
        st.markdown("#### 透视校正")
        warped_path = result.get("warped_path")
        if warped_path and Path(warped_path).exists():
            st.image(warped_preview_path or warped_path, use_container_width=True)

    with columns[2]:
        st.markdown(f"#### {mode_labels.get(result.get('enhance_mode'), '清晰图')}")
        enhanced_path = result.get("enhanced_path")
        if enhanced_path and Path(enhanced_path).exists():
            st.image(enhanced_preview_path or enhanced_path, use_container_width=True)

    if result.get("corners"):
        with st.expander("检测到的文档四角", expanded=False):
            st.json(result["corners"])

    debug_path = result.get("debug_path")
    if debug_path and Path(debug_path).exists():
        debug_preview_path = create_preview_image(debug_path, suffix="debug_preview")
        with st.expander("边界检测调试图", expanded=False):
            st.image(debug_preview_path, use_container_width=True)

    enhanced_path = result.get("enhanced_path")
    if enhanced_path and Path(enhanced_path).exists():
        st.download_button(
            "下载清晰增强图",
            data=Path(enhanced_path).read_bytes(),
            file_name=Path(enhanced_path).name,
            mime="image/png",
        )


def _json_dumps_for_download(payload: dict[str, Any]) -> bytes:
    import json

    slim_payload = {key: value for key, value in payload.items() if key != "raw"}
    return json.dumps(slim_payload, ensure_ascii=False, indent=2).encode("utf-8")


def _show_paper_cut_page() -> None:
    st.subheader("试卷切题")
    st.caption("调用腾讯云 QuestionSplitOCR，将整页试卷切成题目并返回文字和坐标。")

    uploaded_file = st.file_uploader(
        "上传整页试卷图片",
        type=["png", "jpg", "jpeg", "webp", "bmp"],
        key="paper_cut_uploader",
    )
    if not uploaded_file:
        st.info("请上传一张练习册、试卷或教辅整页图片。")
        return

    file_signature = _uploaded_file_signature(uploaded_file)
    if st.session_state.get("paper_cut_file_signature") != file_signature:
        st.session_state["paper_cut_file_signature"] = file_signature
        st.session_state.pop("paper_cut_result", None)
        st.session_state.pop("paper_cut_original_path", None)
        st.session_state.pop("paper_cut_processed", None)

    col_a, col_b = st.columns(2)
    with col_a:
        use_new_model = st.toggle("使用新模型", value=True)
    with col_b:
        enable_image_crop = st.toggle("腾讯云二次切边/弯曲矫正", value=False)

    if st.button("调用腾讯云切题", type="primary"):
        clear_runtime_files()
        cleanup_runtime_files(force=True)
        st.session_state.pop("paper_cut_result", None)
        st.session_state.pop("paper_cut_original_path", None)
        st.session_state.pop("paper_cut_processed", None)
        original_path = _save_processing_upload(uploaded_file)
        processing_result = process_document_image(original_path, enhance_mode="strong")
        enhanced_path = processing_result.get("enhanced_path")
        if (
            processing_result.get("status") == "failed"
            or not enhanced_path
            or not Path(enhanced_path).exists()
        ):
            st.error(f"图片增强失败，无法送入腾讯云 OCR：{processing_result.get('message', '未知错误')}")
            return

        with st.spinner("正在调用腾讯云试卷切题识别..."):
            try:
                result = recognize_question_split(
                    str(enhanced_path),
                    use_new_model=use_new_model,
                    enable_image_crop=enable_image_crop,
                )
            except Exception as exc:
                st.error(str(exc))
                return

        if result.get("question_count", 0) == 0:
            st.warning("腾讯云未识别到题目区域，已保留原始返回结果。")

        st.success(f"切题完成，共识别 {result['question_count']} 个题目区域。")
        result["original_image_path"] = original_path
        result["processing"] = processing_result
        result["original_preview_path"] = create_preview_image(
            original_path,
            task_id=result["task_id"],
            suffix="original_preview",
        )
        result["api_preview_path"] = create_preview_image(
            result["image_path"],
            task_id=result["task_id"],
            suffix="api_preview",
        )
        st.session_state["paper_cut_result"] = result
        st.session_state["paper_cut_original_path"] = original_path
        st.session_state.pop("paper_cut_processed", None)

    result = st.session_state.get("paper_cut_result")
    if not result:
        st.image(_uploaded_preview(uploaded_file), caption="待切题试卷", use_container_width=True)
        return

    original_path = st.session_state.get("paper_cut_original_path")
    original_preview_path = result.get("original_preview_path") or original_path
    api_preview_path = result.get("api_preview_path") or result.get("image_path")
    preview_cols = st.columns(2)
    with preview_cols[0]:
        st.markdown("#### 原图")
        if original_preview_path and Path(original_preview_path).exists():
            st.image(original_preview_path, use_container_width=True)
    with preview_cols[1]:
        st.markdown("#### 增强后送检图")
        if api_preview_path and Path(api_preview_path).exists():
            st.image(api_preview_path, use_container_width=True)

    processing = result.get("processing")
    if processing:
        with st.expander("图片增强信息", expanded=False):
            st.json(
                {
                    "status": processing.get("status"),
                    "message": processing.get("message"),
                    "enhance_mode": processing.get("enhance_mode"),
                    "corners": processing.get("corners"),
                    "enhanced_path": processing.get("enhanced_path"),
                    "api_image_meta": result.get("api_image_meta"),
                }
            )

    st.markdown("#### 切题结果")
    st.caption(f"RequestId: {result.get('request_id') or '-'}")

    for item in result.get("questions", []):
        title_text = item.get("text") or "无文字"
        title = f"第 {item.get('question_no', item.get('subject_index'))} 题 · {title_text[:36]}"
        with st.expander(title, expanded=False):
            crop_path = item.get("crop_path")
            if crop_path and Path(crop_path).exists():
                st.image(crop_path, caption="题目区域", use_container_width=True)
            st.text_area(
                "识别文字",
                item.get("text", ""),
                height=120,
                key=(
                    f"paper_cut_text_{result.get('task_id')}_"
                    f"{item.get('page_index')}_{item.get('subject_index')}"
                ),
            )
            st.json(
                {
                    "bbox": item.get("bbox"),
                    "coord": item.get("coord"),
                    "source": item.get("source"),
                }
            )

    st.download_button(
        "下载切题 JSON",
        data=_json_dumps_for_download(result),
        file_name=f"paper_cut_{result.get('task_id')}.json",
        mime="application/json",
    )



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

    question_count = len(result.get("paper_cut_questions") or result.get("questions", [])) if result else "-"
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("状态", status_text)
    col_b.metric("总分", result.get("score", "-") if result else status.get("score", "-"))
    col_c.metric("正确率", _correct_rate(result.get("questions", [])) if result else "-")
    col_d.metric("识别题数", question_count)

    if status.get("error"):
        st.error(status["error"])

    image_path = None
    if result:
        image_path = result.get("image_path")
    image_path = image_path or status.get("image_path")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("#### 原始作业图")
        image_preview_path = result.get("image_preview_path") if result else image_path
        if image_preview_path and Path(image_preview_path).exists():
            st.image(image_preview_path, use_container_width=True)
        else:
            st.info("暂无可显示的原图。")

        ocr_image_path = result.get("ocr_image_path") if result else None
        ocr_preview_path = result.get("ocr_preview_path") if result else ocr_image_path
        if ocr_preview_path and Path(ocr_preview_path).exists():
            st.markdown("#### 增强后送检图")
            st.image(ocr_preview_path, use_container_width=True)

    with right:
        st.markdown("#### 批改结果")
        if not result:
            st.info("任务尚未完成。点击刷新可查看最新状态。")
            return

        if result.get("status") == "failed":
            st.error(result.get("error") or "任务处理失败。")
            return

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

    if result and result.get("status") == "finished":
        st.markdown("#### 逐题批改")
        for item in result.get("questions", []):
            is_correct = item.get("is_correct")
            badge = "正确" if is_correct is True else "错误" if is_correct is False else "待判断"
            score_text = f"{item.get('score', '-')} / {item.get('max_score', '-')}"
            with st.expander(f"第 {item.get('question_no', '-')} 题 · {badge} · {score_text}", expanded=False):
                cols = st.columns(3)
                cols[0].metric("本题得分", item.get("score", "-"))
                cols[1].metric("本题满分", item.get("max_score", "-"))
                cols[2].metric("可信度", item.get("confidence", "-"))

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
    cleanup_runtime_files()
    start_workers()

    st.title("中小学作业智能批改系统")
    st.caption("Streamlit + 任务队列 + 腾讯云切题 OCR + 大模型批改")

    with st.sidebar:
        st.header("导航")
        page = st.radio("页面", ["上传批改", "图片加工", "试卷切题", "任务列表", "项目说明"], label_visibility="collapsed")
        if st.button("刷新状态", use_container_width=True):
            st.rerun()

    if page == "上传批改":
        uploaded_file = st.file_uploader(
            "上传作业图片",
            type=["png", "jpg", "jpeg", "webp", "bmp"],
            key="correction_uploader",
        )
        if uploaded_file:
            file_signature = _uploaded_file_signature(uploaded_file)
            if st.session_state.get("correction_file_signature") != file_signature:
                st.session_state["correction_file_signature"] = file_signature
                st.session_state.pop("selected_task_id", None)

            st.image(_uploaded_preview(uploaded_file), caption="待批改作业", use_container_width=True)

        if st.button("提交批改任务", type="primary", disabled=uploaded_file is None):
            task_id = submit_task(uploaded_file)
            st.session_state["selected_task_id"] = task_id
            st.success(f"任务已提交：{task_id}")

        selected_task_id = st.session_state.get("selected_task_id")
        if selected_task_id:
            st.divider()
            _show_result(selected_task_id)

    elif page == "图片加工":
        _show_image_processing_page()

    elif page == "试卷切题":
        _show_paper_cut_page()

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

主批改流程需要腾讯云切题 OCR 密钥。若只想演示批改展示，可将大模型设置为 mock：

```text
TENCENT_SECRET_ID=你的腾讯云SecretId
TENCENT_SECRET_KEY=你的腾讯云SecretKey
TENCENT_OCR_REGION=ap-guangzhou
LLM_MODE=mock
```

如果要调用 DeepSeek V4 Flash 做真实批改，请在本地 `.env` 或 Streamlit Secrets 中配置：

```text
LLM_MODE=api
LLM_API_KEY=你的DeepSeek API Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_MAX_TOKENS=4096
DEEPSEEK_THINKING=disabled
```
"""
        )


if __name__ == "__main__":
    main()
