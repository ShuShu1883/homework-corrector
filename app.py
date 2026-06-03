from __future__ import annotations

import uuid
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from PIL import Image, ImageOps
import qrcode
import streamlit as st

from auth import AuthValidationError, authenticate_user, register_user
from config import UPLOAD_DIR, ensure_runtime_dirs
from image_processing import create_preview_image, process_document_image
from mobile_capture import (
    MobileCaptureError,
    create_mobile_capture,
    get_mobile_capture_image,
    get_mobile_capture_status,
    save_mobile_capture_upload,
)
from paper_cut_tencent import recognize_question_split
from runtime_cleanup import cleanup_runtime_files
from storage import list_results, load_result
from task_queue import get_task_status, list_tasks, start_workers, submit_task
from ui_theme import (
    apply_app_theme,
    build_task_card_html,
    render_auth_hero,
    render_brand_header,
    render_login_heading,
    render_page_intro,
    render_sidebar_identity,
    render_steps,
    task_card_button_key,
)


STATUS_LABELS = {
    "waiting": "等待中",
    "running": "处理中",
    "finished": "已完成",
    "failed": "失败",
    "unknown": "未知",
}

IMAGE_INPUT_TYPES = ["png", "jpg", "jpeg", "webp", "bmp"]


class _NamedImageInput:
    def __init__(self, uploaded_file: Any, name: str) -> None:
        self._uploaded_file = uploaded_file
        self.name = name

    @property
    def size(self) -> int:
        return len(bytes(self.getbuffer()))

    def getbuffer(self) -> Any:
        return self._uploaded_file.getbuffer()


def _current_username() -> str | None:
    username = st.session_state.get("username")
    return str(username) if username else None


def _logout_session() -> None:
    st.session_state.clear()


def _register_from_form(username: str, password: str, password_confirmation: str) -> str:
    if password != password_confirmation:
        raise AuthValidationError("两次输入的密码不一致。")
    return register_user(username, password)


def _show_auth_page() -> None:
    hero_col, form_col = st.columns([1.12, 0.88], gap="large")
    with hero_col:
        render_auth_hero()

    with form_col:
        render_login_heading()
        login_tab, register_tab = st.tabs(["登录", "注册"])

        with login_tab:
            with st.form("login_form"):
                username = st.text_input("用户名", key="login_username")
                password = st.text_input("密码", type="password", key="login_password")
                submitted = st.form_submit_button("登录", type="primary", width="stretch")

            if submitted:
                try:
                    authenticated_username = authenticate_user(username, password)
                except RuntimeError as exc:
                    st.error(str(exc))
                else:
                    if authenticated_username:
                        st.session_state["username"] = authenticated_username
                        st.rerun()
                    else:
                        st.error("用户名或密码错误。")

        with register_tab:
            st.warning("当前为简化账号系统，密码会保存在本地文件中。请勿使用其他网站的常用密码。")
            with st.form("register_form"):
                username = st.text_input("注册用户名", key="register_username")
                password = st.text_input("注册密码", type="password", key="register_password")
                password_confirmation = st.text_input(
                    "确认密码",
                    type="password",
                    key="register_password_confirmation",
                )
                submitted = st.form_submit_button("注册并登录", type="primary", width="stretch")

            if submitted:
                try:
                    registered_username = _register_from_form(username, password, password_confirmation)
                except (AuthValidationError, RuntimeError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state["username"] = registered_username
                    st.rerun()


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


def _uploaded_file_size(uploaded_file: Any, data: bytes) -> int:
    size = getattr(uploaded_file, "size", None)
    try:
        return int(size)
    except (TypeError, ValueError):
        return len(data)


def _uploaded_file_name(uploaded_file: Any, *, source: str, digest: str) -> str:
    name = str(getattr(uploaded_file, "name", "") or "").strip()
    if name:
        return name
    if source in {"camera", "mobile"}:
        return f"camera_{digest}.jpg"
    return "uploaded_image.png"


def _uploaded_file_signature(uploaded_file: Any, *, source: str = "upload") -> str:
    data = bytes(uploaded_file.getbuffer())
    digest = hashlib.blake2b(data, digest_size=8).hexdigest()
    name = _uploaded_file_name(uploaded_file, source=source, digest=digest)
    size = _uploaded_file_size(uploaded_file, data)
    return f"{source}:{name}:{size}:{digest}"


def _with_camera_filename(uploaded_file: Any, key_prefix: str) -> Any:
    data = bytes(uploaded_file.getbuffer())
    digest = hashlib.blake2b(data, digest_size=8).hexdigest()
    return _NamedImageInput(uploaded_file, f"camera_{key_prefix}_{digest}.jpg")


def _query_param(name: str) -> str | None:
    value = st.query_params.get(name)
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value else None


def _is_mobile_user_agent(user_agent: str | None) -> bool:
    normalized = str(user_agent or "").lower()
    if not normalized:
        return False
    mobile_markers = ("mobile", "android", "iphone", "ipad", "ipod")
    return any(marker in normalized for marker in mobile_markers)


def is_mobile_client() -> bool:
    try:
        user_agent = st.context.headers.get("user-agent")
    except Exception:
        return False
    return _is_mobile_user_agent(user_agent)


def _mobile_capture_url(token: str) -> str:
    current_url = str(getattr(st.context, "url", "") or "")
    parsed = urlparse(current_url)
    if not parsed.scheme or not parsed.netloc:
        parsed = urlparse("http://127.0.0.1:8501/")
    query = urlencode({"mobile_capture": token})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", query, ""))


def _qr_png_bytes(url: str) -> bytes:
    image = qrcode.make(url)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _clear_mobile_capture_state(key_prefix: str) -> None:
    st.session_state.pop(f"{key_prefix}_mobile_token", None)
    st.session_state.pop(f"{key_prefix}_mobile_file", None)
    st.session_state.pop(f"{key_prefix}_mobile_signature", None)


def _clear_desktop_camera_state(key_prefix: str) -> None:
    st.session_state.pop(f"{key_prefix}_camera_open", None)
    st.session_state.pop(f"{key_prefix}_camera", None)


def _create_mobile_capture_for_session(key_prefix: str, owner_username: str) -> str:
    capture = create_mobile_capture(owner_username, key_prefix)
    token = str(capture["token"])
    st.session_state[f"{key_prefix}_mobile_token"] = token
    st.session_state.pop(f"{key_prefix}_mobile_file", None)
    st.session_state.pop(f"{key_prefix}_mobile_signature", None)
    return token


def _select_mobile_capture_input(key_prefix: str, owner_username: str) -> Any | None:
    token_key = f"{key_prefix}_mobile_token"
    file_key = f"{key_prefix}_mobile_file"
    if not st.session_state.get(token_key):
        _create_mobile_capture_for_session(key_prefix, owner_username)

    token = str(st.session_state.get(token_key))
    status = get_mobile_capture_status(token)
    if not status:
        st.warning("手机拍照链接已过期，请重新生成二维码。")
        if st.button("重新生成手机拍照二维码", key=f"{key_prefix}_mobile_refresh_expired"):
            _create_mobile_capture_for_session(key_prefix, owner_username)
            st.rerun()
        return None

    mobile_url = _mobile_capture_url(token)
    col_qr, col_info = st.columns([0.34, 0.66], gap="large")
    with col_qr:
        st.image(_qr_png_bytes(mobile_url), caption="用手机扫码拍照", width=220)
    with col_info:
        st.markdown("#### 手机拍照上传")
        st.write("手机扫码后会打开一次性拍照页，不需要再次登录。")
        st.caption(f"有效期至：{status.get('expires_at', '-')}")
        st.code(mobile_url, language=None)
        st.caption("如果二维码里是 127.0.0.1 或 localhost，手机无法访问；请用电脑局域网 IP 打开本页后重新生成二维码。")
        if st.button("重新生成二维码", key=f"{key_prefix}_mobile_refresh"):
            _create_mobile_capture_for_session(key_prefix, owner_username)
            st.rerun()

    if st.button("检查手机照片", key=f"{key_prefix}_mobile_check", type="primary"):
        mobile_file = get_mobile_capture_image(token, owner_username)
        if mobile_file:
            st.session_state[file_key] = mobile_file
            st.session_state[f"{key_prefix}_mobile_signature"] = _uploaded_file_signature(
                mobile_file,
                source="mobile",
            )
            st.success("已收到手机照片。")
        else:
            st.info("还没有收到手机照片，请在手机页面拍照或上传后再检查。")

    return st.session_state.get(file_key)


def _select_image_input(
    *,
    key_prefix: str,
    uploader_label: str,
    camera_label: str,
    owner_username: str,
    mobile_client: bool | None = None,
) -> tuple[Any | None, str | None]:
    if mobile_client is None:
        mobile_client = is_mobile_client()
    if mobile_client:
        _clear_desktop_camera_state(key_prefix)
        _clear_mobile_capture_state(key_prefix)
        st.caption("手机端可直接选择图片，系统通常会提供拍照或从相册选择。")
        uploaded_file = st.file_uploader(
            "选择图片或拍照上传",
            type=IMAGE_INPUT_TYPES,
            key=f"{key_prefix}_mobile_direct_upload",
        )
        return (uploaded_file, "upload") if uploaded_file else (None, None)

    source_key = f"{key_prefix}_image_source"
    source = st.segmented_control(
        "图片来源",
        ["上传图片", "电脑摄像头", "手机拍照"],
        key=source_key,
        default="上传图片",
        width="stretch",
    )
    previous_source_key = f"{key_prefix}_previous_image_source"
    previous_source = st.session_state.get(previous_source_key)
    if previous_source != source:
        st.session_state[previous_source_key] = source
        if source == "上传图片":
            _clear_desktop_camera_state(key_prefix)
            _clear_mobile_capture_state(key_prefix)
        elif source == "电脑摄像头":
            _clear_mobile_capture_state(key_prefix)
        elif source == "手机拍照":
            _clear_desktop_camera_state(key_prefix)

    if source == "上传图片":
        uploaded_file = st.file_uploader(
            uploader_label,
            type=IMAGE_INPUT_TYPES,
            key=f"{key_prefix}_uploader",
        )
        return (uploaded_file, "upload") if uploaded_file else (None, None)

    if source == "电脑摄像头":
        st.caption("浏览器会请求当前电脑的摄像头权限；线上访问通常需要 HTTPS，localhost 本地调试可直接使用。")
        camera_open_key = f"{key_prefix}_camera_open"
        if not st.session_state.get(camera_open_key):
            st.info("点击下方按钮后才会打开摄像头区域。Streamlit 不能直接弹出系统相机，需要先渲染摄像头组件。")
            if st.button("打开电脑摄像头", key=f"{key_prefix}_camera_open_button", type="primary"):
                st.session_state[camera_open_key] = True
                st.rerun()
            return None, None

        if st.button("关闭摄像头", key=f"{key_prefix}_camera_close_button"):
            _clear_desktop_camera_state(key_prefix)
            st.rerun()
        camera_file = st.camera_input(camera_label, key=f"{key_prefix}_camera")
        if camera_file:
            return _with_camera_filename(camera_file, key_prefix), "camera"
        return None, None

    mobile_file = _select_mobile_capture_input(key_prefix, owner_username)
    return (mobile_file, "mobile") if mobile_file else (None, None)


def _show_mobile_capture_page(token: str) -> None:
    render_page_intro("手机拍照上传", "选择图片或调用手机系统拍照，上传后回到电脑页面点击“检查手机照片”。", kicker="Mobile capture ✦")
    status = get_mobile_capture_status(token)
    if not status:
        st.error("手机拍照链接已过期或不存在，请回到电脑端重新生成二维码。")
        return
    if status.get("used"):
        st.success("照片已经上传成功，可以回到电脑端检查手机照片。")
        return

    st.caption(f"链接有效期至：{status.get('expires_at', '-')}")
    selected_file = st.file_uploader(
        "选择图片或拍照上传",
        type=IMAGE_INPUT_TYPES,
        key=f"mobile_upload_{token}",
    )
    st.caption("手机浏览器通常会在这里提供“拍照”或“从相册选择”。如果没有拍照选项，请先用系统相机拍好后从相册选择。")
    if st.button("上传到电脑页面", type="primary", disabled=selected_file is None, width="stretch"):
        try:
            save_mobile_capture_upload(token, selected_file)
        except MobileCaptureError as exc:
            st.error(str(exc))
        else:
            st.success("上传成功，请回到电脑页面点击“检查手机照片”。")
            st.rerun()


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
    cols = st.columns(3)
    cols[0].metric("本题得分", item.get("score", "-"))
    cols[1].metric("本题满分", item.get("max_score", "-"))
    cols[2].metric("可信度", item.get("confidence", "-"))

    crop_path = paper_cut_question.get("crop_path")
    if crop_path and Path(crop_path).exists():
        with st.expander("查看本题区域", expanded=False):
            st.image(crop_path, width="stretch")

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


def _save_processing_upload(uploaded_file: Any, *, source: str = "upload") -> str:
    ensure_runtime_dirs()
    data = bytes(uploaded_file.getbuffer())
    digest = hashlib.blake2b(data, digest_size=8).hexdigest()
    filename = _uploaded_file_name(uploaded_file, source=source, digest=digest)
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        suffix = ".jpg" if source in {"camera", "mobile"} else ".png"

    image_path = UPLOAD_DIR / f"processing_{uuid.uuid4()}{suffix}"
    image_path.write_bytes(data)
    return str(image_path)


def _show_image_processing_page() -> None:
    render_page_intro("图片加工", "自动检测文档边界，裁边拉正，并增强文字效果。", kicker="Image lab ✦")
    render_steps(["上传整页作业图", "选择清晰增强模式", "下载处理后的图片"])

    uploaded_file, image_source = _select_image_input(
        key_prefix="processing",
        uploader_label="上传需要加工的作业图片",
        camera_label="拍摄需要加工的作业图片",
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
        st.image(original_path, caption="原图", width="stretch")
        return

    columns = st.columns(3)
    with columns[0]:
        st.markdown("#### 原图")
        st.image(original_preview_path, width="stretch")

    with columns[1]:
        st.markdown("#### 透视校正")
        warped_path = result.get("warped_path")
        if warped_path and Path(warped_path).exists():
            st.image(warped_preview_path or warped_path, width="stretch")

    with columns[2]:
        st.markdown(f"#### {mode_labels.get(result.get('enhance_mode'), '清晰图')}")
        enhanced_path = result.get("enhanced_path")
        if enhanced_path and Path(enhanced_path).exists():
            st.image(enhanced_preview_path or enhanced_path, width="stretch")

    if result.get("corners"):
        with st.expander("检测到的文档四角", expanded=False):
            st.json(result["corners"])

    debug_path = result.get("debug_path")
    if debug_path and Path(debug_path).exists():
        debug_preview_path = create_preview_image(debug_path, suffix="debug_preview")
        with st.expander("边界检测调试图", expanded=False):
            st.image(debug_preview_path, width="stretch")

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
    render_page_intro(
        "试卷切题",
        "调用腾讯云 QuestionSplitOCR，将整页试卷切成题目并返回文字和坐标。",
        kicker="Question split ✦",
    )
    render_steps(["上传完整试卷", "设置识别选项", "查看并下载切题结果"])

    uploaded_file, image_source = _select_image_input(
        key_prefix="paper_cut",
        uploader_label="上传整页试卷图片",
        camera_label="拍摄整页试卷图片",
        owner_username=_current_username() or "",
    )
    if not uploaded_file:
        st.info("请上传或拍摄一张练习册、试卷或教辅整页图片。")
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

    if st.button("调用腾讯云切题", type="primary"):
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
        st.image(_uploaded_preview(uploaded_file), caption="待切题试卷", width="stretch")
        return

    original_path = st.session_state.get("paper_cut_original_path")
    original_preview_path = result.get("original_preview_path") or original_path
    api_preview_path = result.get("api_preview_path") or result.get("image_path")
    preview_cols = st.columns(2)
    with preview_cols[0]:
        st.markdown("#### 原图")
        if original_preview_path and Path(original_preview_path).exists():
            st.image(original_preview_path, width="stretch")
    with preview_cols[1]:
        st.markdown("#### 增强后送检图")
        if api_preview_path and Path(api_preview_path).exists():
            st.image(api_preview_path, width="stretch")

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
        "下载切题 JSON",
        data=_json_dumps_for_download(result),
        file_name=f"paper_cut_{result.get('task_id')}.json",
        mime="application/json",
    )



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
            st.markdown("#### 批注后送检图")
            annotated_image_path = result.get("annotated_image_path")
            if annotated_image_path and Path(annotated_image_path).exists():
                st.image(annotated_image_path, width="stretch")
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
        if image_preview_path and Path(image_preview_path).exists():
            st.image(image_preview_path, width="stretch")
        else:
            st.info("暂无可显示的原图。")

        ocr_image_path = result.get("ocr_image_path")
        ocr_preview_path = result.get("ocr_preview_path") or ocr_image_path
        if ocr_preview_path and Path(ocr_preview_path).exists():
            st.markdown("#### 增强后送检图")
            st.image(ocr_preview_path, width="stretch")

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
    st.set_page_config(page_title="智能作业批改系统", page_icon="✦", layout="wide")
    ensure_runtime_dirs()
    apply_app_theme()

    mobile_capture_token = _query_param("mobile_capture")
    if mobile_capture_token:
        _show_mobile_capture_page(mobile_capture_token)
        return

    owner_username = _current_username()
    if not owner_username:
        _show_auth_page()
        return

    cleanup_runtime_files()
    start_workers()

    with st.sidebar:
        render_sidebar_identity(owner_username)
        page = st.radio("页面", ["上传批改", "图片加工", "试卷切题", "任务列表", "项目说明"], label_visibility="collapsed")
        if st.button("刷新状态", width="stretch"):
            st.rerun()
        if st.button("退出登录", width="stretch"):
            _logout_session()
            st.rerun()

    render_brand_header()

    if page == "上传批改":
        render_page_intro("上传批改", "提交一张作业图片，后台队列会依次完成切题、识别和智能批改。", kicker="Homework correction ✦")
        render_steps(["上传作业图片", "提交后台批改", "刷新并查看报告"])
        uploaded_file, image_source = _select_image_input(
            key_prefix="correction",
            uploader_label="上传作业图片",
            camera_label="拍摄作业图片",
            owner_username=owner_username,
        )
        if uploaded_file:
            file_signature = _uploaded_file_signature(uploaded_file, source=image_source or "upload")
            if st.session_state.get("correction_file_signature") != file_signature:
                st.session_state["correction_file_signature"] = file_signature
                st.session_state.pop("selected_task_id", None)

            st.image(_uploaded_preview(uploaded_file), caption="待批改作业", width="stretch")

        if st.button("提交批改任务", type="primary", disabled=uploaded_file is None):
            task_id = submit_task(uploaded_file, owner_username)
            st.session_state["selected_task_id"] = task_id
            st.success(f"任务已提交：{task_id}")

        selected_task_id = st.session_state.get("selected_task_id")
        if selected_task_id:
            st.divider()
            _show_result(selected_task_id, owner_username)

    elif page == "图片加工":
        _show_image_processing_page()

    elif page == "试卷切题":
        _show_paper_cut_page()

    elif page == "任务列表":
        render_page_intro("任务列表", "集中查看自己的历史批改记录和当前处理进度。", kicker="My homework archive ✦")
        rows = _task_rows(owner_username)
        if not rows:
            st.info("暂无任务。")
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

    else:
        render_page_intro("项目说明", "了解当前系统的运行结构、适用范围和演示配置。", kicker="About this project ✦")
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

如果要调用 DeepSeek V4 Pro 做真实批改，请在本地 `.env` 或 Streamlit Secrets 中配置：

```text
LLM_MODE=api
LLM_API_KEY=你的DeepSeek API Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
LLM_MAX_TOKENS=4096
DEEPSEEK_THINKING=disabled
```
"""
        )


if __name__ == "__main__":
    main()
