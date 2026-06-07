from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from auth import (
    AuthValidationError,
    authenticate_user,
    delete_user,
    get_display_name,
    register_user,
    update_display_name,
)
from config import CUT_DIR, PROCESSED_DIR, UPLOAD_DIR
from storage import delete_results
from time_utils import beijing_now_iso
from ui_theme import render_auth_hero, render_login_heading, render_page_intro


RESULT_FILE_PATH_KEYS = (
    "image_path",
    "image_preview_path",
    "enhanced_image_path",
    "ocr_image_path",
    "ocr_preview_path",
    "annotated_image_path",
)


def _current_display_name(owner_username: str) -> str:
    return get_display_name(owner_username) or owner_username


def _safe_runtime_file(path_value: Any) -> Path | None:
    if not path_value:
        return None
    try:
        path = Path(str(path_value)).resolve()
    except (OSError, ValueError):
        return None
    runtime_roots = [UPLOAD_DIR, PROCESSED_DIR, CUT_DIR]
    for root in runtime_roots:
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        return path if path.is_file() else None
    return None


def _collect_result_file_paths(results: list[dict[str, Any]]) -> set[Path]:
    paths: set[Path] = set()
    for result in results:
        for key in RESULT_FILE_PATH_KEYS:
            path = _safe_runtime_file(result.get(key))
            if path:
                paths.add(path)
        processing = result.get("processing")
        if isinstance(processing, dict):
            for value in processing.values():
                path = _safe_runtime_file(value)
                if path:
                    paths.add(path)
        api_meta = result.get("api_image_meta")
        if isinstance(api_meta, dict):
            for key in ("source_image_path", "api_image_path"):
                path = _safe_runtime_file(api_meta.get(key))
                if path:
                    paths.add(path)
        for item in result.get("paper_cut_questions", []):
            if isinstance(item, dict):
                path = _safe_runtime_file(item.get("crop_path"))
                if path:
                    paths.add(path)
    return paths


def _delete_account(owner_username: str, confirmation: str) -> bool:
    if str(confirmation or "").strip().lower() != owner_username:
        return False
    removed_results = delete_results(owner_username)
    for path in _collect_result_file_paths(removed_results):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    delete_user(owner_username)
    return True


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
                    st.session_state["_show_display_name_editor"] = True
                    st.rerun()


def _show_display_name_editor(owner_username: str, *, mandatory: bool = False) -> None:
    title = "设置用户名" if mandatory else "编辑用户名"
    description = "用户名会用于侧边栏和学习排行榜展示，不影响登录账号。"
    render_page_intro(title, description, kicker="Profile")
    with st.form("display_name_form"):
        display_name = st.text_input(
            "用户名",
            value=_current_display_name(owner_username),
            max_chars=40,
            key="display_name_input",
        )
        submitted = st.form_submit_button("保存用户名", type="primary", width="stretch")
    if submitted:
        try:
            update_display_name(owner_username, display_name)
        except AuthValidationError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("_show_display_name_editor", None)
            st.session_state.pop("_show_profile_editor", None)
            st.success("用户名已保存。")
            st.rerun()
    if not mandatory and st.button("取消", key="cancel_display_name_editor", width="stretch"):
        st.session_state.pop("_show_profile_editor", None)
        st.rerun()
