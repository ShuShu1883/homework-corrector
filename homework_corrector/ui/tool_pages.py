from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from homework_corrector.core.app_state import _current_username
from homework_corrector.ui.image_inputs import (
    _save_processing_upload,
    _select_image_input,
    _uploaded_file_signature,
    _uploaded_preview,
)
from homework_corrector.processing.image_processing import create_preview_image, process_document_image
from homework_corrector.processing.paper_cut_tencent import recognize_question_split
from homework_corrector.core.resource_paths import display_resource, is_http_url, resource_exists
from homework_corrector.storage.result_assets import delete_local_files, upload_result_assets
from homework_corrector.ui.ui_theme import render_page_intro, render_steps


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

    owner_username = _current_username() or "anonymous"
    operation_id = str(uuid.uuid4())
    original_path = _save_processing_upload(uploaded_file, source=image_source or "upload")
    result = process_document_image(
        original_path,
        task_id=operation_id,
        enhance_mode=selected_mode or "strong",
    )
    enhanced_preview_path = (
        create_preview_image(result["enhanced_path"], task_id=operation_id, suffix="enhanced_preview")
        if result.get("enhanced_path")
        else None
    )
    if enhanced_preview_path:
        result["enhanced_preview_path"] = enhanced_preview_path
    result["task_id"] = operation_id
    result["owner_username"] = owner_username

    status = result.get("status")
    if status == "success":
        st.success(result.get("message"))
    elif status == "fallback":
        st.warning(result.get("message"))
    else:
        st.error(result.get("message") or "图片处理失败。")
        return

    enhanced_path = result.get("enhanced_path")
    download_data = Path(enhanced_path).read_bytes() if enhanced_path and Path(str(enhanced_path)).is_file() else None
    download_name = Path(str(enhanced_path)).name if enhanced_path else "enhanced.png"
    result, local_paths = upload_result_assets(result)
    if result.get("storage_backend") == "cos":
        delete_local_files(local_paths)

    enhanced_path = result.get("enhanced_path")
    enhanced_preview_path = result.get("enhanced_preview_path") or enhanced_preview_path
    if enhanced_path and resource_exists(enhanced_path):
        st.markdown(f"#### {mode_labels.get(result.get('enhance_mode'), '清晰图')}")
        st.image(display_resource(enhanced_preview_path or enhanced_path), width="stretch")
        if download_data is not None:
            st.download_button(
                "下载清晰增强图",
                data=download_data,
                file_name=download_name,
                mime="image/png",
            )
        elif is_http_url(enhanced_path):
            st.link_button("打开清晰增强图", str(enhanced_path))


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
        result["owner_username"] = _current_username() or "anonymous"
        result, local_paths = upload_result_assets(result)
        if result.get("storage_backend") == "cos":
            delete_local_files(local_paths)
        st.session_state["paper_cut_result"] = result
        st.session_state["paper_cut_original_path"] = original_path
        st.session_state.pop("paper_cut_processed", None)

    result = st.session_state.get("paper_cut_result")
    if not result:
        st.image(_uploaded_preview(uploaded_file), caption="待识别作业", width="stretch")
        return

    api_preview_path = result.get("api_preview_path") or result.get("image_path")
    st.markdown("#### 增强后识别图")
    if api_preview_path and resource_exists(api_preview_path):
        st.image(display_resource(api_preview_path), width="stretch")

    st.markdown("#### 题目识别结果")
    st.caption(f"RequestId: {result.get('request_id') or '-'}")

    for item in result.get("questions", []):
        title_text = item.get("text") or "无文字"
        title = f"第 {item.get('question_no', item.get('subject_index'))} 题 · {title_text[:36]}"
        with st.expander(title, expanded=False):
            crop_path = item.get("crop_path")
            if crop_path and resource_exists(crop_path):
                st.image(display_resource(crop_path), caption="题目区域", width="stretch")
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

