from __future__ import annotations

import hashlib
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from PIL import Image, ImageOps
import qrcode
import streamlit as st

from app_state import _current_username, _query_param, is_mobile_client
from config import UPLOAD_DIR, ensure_runtime_dirs
from mobile_capture import (
    MobileCaptureError,
    create_mobile_capture,
    get_mobile_capture_image,
    get_mobile_capture_status,
    save_mobile_capture_upload,
)
from score_utils import IMAGE_INPUT_TYPES
from ui_theme import render_page_intro


class _NamedImageInput:
    def __init__(self, uploaded_file: Any, name: str) -> None:
        self._uploaded_file = uploaded_file
        self.name = name

    @property
    def size(self) -> int:
        return len(bytes(self.getbuffer()))

    def getbuffer(self) -> Any:
        return self._uploaded_file.getbuffer()


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


def _uploaded_files_signature(uploaded_files: list[Any], *, source: str = "upload") -> str:
    signatures = [
        _uploaded_file_signature(uploaded_file, source=source)
        for uploaded_file in uploaded_files
        if uploaded_file
    ]
    return "|".join(signatures)


def _with_camera_filename(uploaded_file: Any, key_prefix: str) -> Any:
    data = bytes(uploaded_file.getbuffer())
    digest = hashlib.blake2b(data, digest_size=8).hexdigest()
    return _NamedImageInput(uploaded_file, f"camera_{key_prefix}_{digest}.jpg")


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
        st.caption("浏览器会请求当前电脑的摄像头权限；线上访问通常需要 HTTPS。")
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


def _select_image_inputs(
    *,
    key_prefix: str,
    uploader_label: str,
    camera_label: str,
    owner_username: str,
    mobile_client: bool | None = None,
) -> tuple[list[Any], str | None]:
    if mobile_client is None:
        mobile_client = is_mobile_client()
    if mobile_client:
        _clear_desktop_camera_state(key_prefix)
        _clear_mobile_capture_state(key_prefix)
        st.caption("手机端可直接选择一张或多张图片，系统通常会提供拍照或从相册选择。")
        uploaded_files = st.file_uploader(
            "选择图片或拍照上传",
            type=IMAGE_INPUT_TYPES,
            key=f"{key_prefix}_mobile_direct_uploads",
            accept_multiple_files=True,
        )
        return (list(uploaded_files), "upload") if uploaded_files else ([], None)

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
        uploaded_files = st.file_uploader(
            uploader_label,
            type=IMAGE_INPUT_TYPES,
            key=f"{key_prefix}_uploader_multi",
            accept_multiple_files=True,
        )
        return (list(uploaded_files), "upload") if uploaded_files else ([], None)

    if source == "电脑摄像头":
        st.caption("浏览器会请求当前电脑的摄像头权限；线上访问通常需要 HTTPS。")
        camera_open_key = f"{key_prefix}_camera_open"
        if not st.session_state.get(camera_open_key):
            st.info("点击下方按钮后才会打开摄像头区域。Streamlit 不能直接弹出系统相机，需要先渲染摄像头组件。")
            if st.button("打开电脑摄像头", key=f"{key_prefix}_camera_open_button", type="primary"):
                st.session_state[camera_open_key] = True
                st.rerun()
            return [], None

        if st.button("关闭摄像头", key=f"{key_prefix}_camera_close_button"):
            _clear_desktop_camera_state(key_prefix)
            st.rerun()
        camera_file = st.camera_input(camera_label, key=f"{key_prefix}_camera")
        if camera_file:
            return [_with_camera_filename(camera_file, key_prefix)], "camera"
        return [], None

    mobile_file = _select_mobile_capture_input(key_prefix, owner_username)
    return ([mobile_file], "mobile") if mobile_file else ([], None)


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
