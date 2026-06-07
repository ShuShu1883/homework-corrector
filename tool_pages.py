from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from app_state import _current_username
from image_inputs import (
    _save_processing_upload,
    _select_image_input,
    _uploaded_file_signature,
    _uploaded_preview,
)
from image_processing import create_preview_image, process_document_image
from paper_cut_tencent import recognize_question_split
from runtime_cleanup import cleanup_runtime_files
from ui_theme import render_page_intro, render_steps


def _show_image_processing_page() -> None:
    render_page_intro("图片增强", "自动检测文档边界，裁边拉正，并增强文字清晰度。", kicker="Image enhancement ✦")
    render_steps(["上传整页作业图", "选择清晰增强模式", "下载处理后的图片"])

    uploaded_file, image_source = _select_image_input(
        key_prefix="processing",
        uploader_label="上传需要增强的作业图片",
        camera_label="拍摄需要增强的作业图片",
        owner_username=_current_username() or "",
    )
    if not uploaded_file:
        st.info("请先上传或拍摄一张整页作业照片或扫描图片。")
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

    original_path = _save_processing_upload(uploaded_file, source=image_source or "upload")
    result = process_document_image(original_path, enhance_mode=selected_mode or "strong")
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
        return

    enhanced_path = result.get("enhanced_path")
    if enhanced_path and Path(enhanced_path).exists():
        st.markdown(f"#### {mode_labels.get(result.get('enhance_mode'), '清晰图')}")
        st.image(enhanced_preview_path or enhanced_path, width="stretch")
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
    render_page_intro(
        "题目识别",
        "识别整页作业中的题目区域，并返回题目文字、坐标和裁剪图。",
        kicker="Question recognition ✦",
    )
    render_steps(["上传完整作业图片", "设置识别选项", "查看并下载识别结果"])

    uploaded_file, image_source = _select_image_input(
        key_prefix="paper_cut",
        uploader_label="上传整页作业图片",
        camera_label="拍摄整页作业图片",
        owner_username=_current_username() or "",
    )
    if not uploaded_file:
        st.info("请上传或拍摄一张练习册、作业或教辅整页图片。")
        return

    file_signature = _uploaded_file_signature(uploaded_file, source=image_source or "upload")
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

    if st.button("开始题目识别", type="primary"):
        cleanup_runtime_files(force=True)
        st.session_state.pop("paper_cut_result", None)
        st.session_state.pop("paper_cut_original_path", None)
        st.session_state.pop("paper_cut_processed", None)
        original_path = _save_processing_upload(uploaded_file, source=image_source or "upload")
        processing_result = process_document_image(original_path, enhance_mode="strong")
        enhanced_path = processing_result.get("enhanced_path")
        if (
            processing_result.get("status") == "failed"
            or not enhanced_path
            or not Path(enhanced_path).exists()
        ):
            st.error(f"图片增强失败，无法送入腾讯云 OCR：{processing_result.get('message', '未知错误')}")
            return

        with st.spinner("正在识别题目区域..."):
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

        st.success(f"识别完成，共找到 {result['question_count']} 个题目区域。")
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
        st.image(_uploaded_preview(uploaded_file), caption="待识别作业", width="stretch")
        return

    api_preview_path = result.get("api_preview_path") or result.get("image_path")
    st.markdown("#### 增强后识别图")
    if api_preview_path and Path(api_preview_path).exists():
        st.image(api_preview_path, width="stretch")

    st.markdown("#### 题目识别结果")
    st.caption(f"RequestId: {result.get('request_id') or '-'}")

    for item in result.get("questions", []):
        title_text = item.get("text") or "无文字"
        title = f"第 {item.get('question_no', item.get('subject_index'))} 题 · {title_text[:36]}"
        with st.expander(title, expanded=False):
            crop_path = item.get("crop_path")
            if crop_path and Path(crop_path).exists():
                st.image(crop_path, caption="题目区域", width="stretch")
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
        "下载识别 JSON",
        data=_json_dumps_for_download(result),
        file_name=f"question_recognition_{result.get('task_id')}.json",
        mime="application/json",
    )


def _project_description_markdown() -> str:
    return """
### 系统定位

本系统面向中小学作业批改场景，提供从作业图片采集、题目识别、OCR 识别到大模型批改反馈的一体化流程。教师或学生上传作业图片后，系统会自动进入后台队列处理，并在页面中展示批改进度和结果。

### 核心能力

- 支持图片上传、电脑摄像头拍照和手机扫码上传三种采集方式。
- 自动增强作业图片清晰度，提升后续 OCR 识别稳定性。
- 调用腾讯云题目识别 OCR 识别题目区域，并保留题目裁剪结果用于核对。
- 结合大模型生成逐题评分、错因分析、订正建议和整体学习反馈。
- 按登录账号隔离批改记录和历史批改结果，避免不同用户互相看到数据。

### 使用流程

系统还提供用户名、学习分析和学习排行榜。学习分析会按当前账号汇总成绩趋势和错题；学习排行榜只展示平均得分率 Top 5，并在本人未上榜时额外显示个人排名。

用户登录后可在“作业批改”页面提交作业图片，任务进入后台队列后可以继续浏览其他页面；在“批改记录”中查看处理状态，任务完成后进入详情页查看总分、正确率、批注图和逐题反馈。“图片增强”和“题目识别”页面提供独立的图像增强与题目识别能力，适合在正式批改前检查图片质量。

### 部署说明

系统部署时需要提供可持久化的运行目录，用于保存账号文件、任务结果和临时图片；同时需要配置 OCR 服务和大模型服务的访问凭据。摄像头拍照能力由浏览器权限控制，线上环境建议使用 HTTPS，以便电脑和手机浏览器正常调用相机或系统图片选择器。

### 数据边界

账号、任务状态和结果文件保存在应用运行目录中。系统按登录账号展示对应任务，不主动公开其他用户的数据。当前版本适合低风险、小规模使用场景；若用于长期公开服务，应进一步接入更完整的账号安全、存储备份、访问控制和运维监控机制。
"""


def _show_project_page() -> None:
    render_page_intro("系统说明", "面向线上使用的作业智能批改与图像处理系统。", kicker="About this project ✦")
    st.markdown(_project_description_markdown())
