from __future__ import annotations

import streamlit as st

from account_pages import (
    _current_display_name,
    _delete_account,
    _show_auth_page,
    _show_display_name_editor,
)
from analysis_pages import _show_leaderboard_page, _show_learning_analysis_page
from app_state import _current_username, _logout_session, _query_param
from config import ensure_runtime_dirs
from image_inputs import (
    _select_image_input,
    _show_mobile_capture_page,
    _uploaded_file_signature,
    _uploaded_preview,
)
from result_views import _show_result, show_records_page
from runtime_cleanup import cleanup_runtime_files
from task_queue import start_workers, submit_task
from tool_pages import _show_image_processing_page, _show_paper_cut_page, _show_project_page
from ui_theme import (
    apply_app_theme,
    render_brand_header,
    render_page_intro,
    render_sidebar_identity,
    render_steps,
)


PAGES = ["作业批改", "图片增强", "题目识别", "批改记录", "学习分析", "学习排行榜", "系统说明"]


def _show_homework_correction_page(owner_username: str) -> None:
    render_page_intro(
        "作业批改",
        "提交一张作业图片，后台队列会依次完成题目识别、内容识别和智能批改。",
        kicker="Homework correction ✦",
    )
    render_steps(["上传作业图片", "提交批改任务", "刷新并查看报告"])
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

    if st.button(
        "提交批改任务",
        type="primary",
        disabled=uploaded_file is None,
        key="submit_correction_task",
    ):
        task_id = submit_task(uploaded_file, owner_username)
        st.session_state["selected_task_id"] = task_id
        st.success(f"任务已提交：{task_id}")

    selected_task_id = st.session_state.get("selected_task_id")
    if selected_task_id:
        st.divider()
        _show_result(selected_task_id, owner_username)


def _render_sidebar(owner_username: str) -> str:
    with st.sidebar:
        render_sidebar_identity(owner_username, _current_display_name(owner_username))
        page = st.radio("页面", PAGES, label_visibility="collapsed", key="main_page")
        if st.button("刷新状态", width="stretch", key="sidebar_refresh"):
            st.rerun()
        if st.button("编辑用户名", width="stretch", key="sidebar_edit_display_name"):
            st.session_state["_show_profile_editor"] = True
            st.rerun()
        if st.session_state.get("_confirm_delete_account"):
            st.error("注销账号会删除账号、批改结果和关联文件，且不可恢复。")
            confirmation = st.text_input("输入用户名确认注销", key="delete_account_confirmation")
            cancel_col, confirm_col = st.columns(2)
            with cancel_col:
                if st.button("取消", width="stretch", key="cancel_delete_account"):
                    st.session_state.pop("_confirm_delete_account", None)
                    st.session_state.pop("delete_account_confirmation", None)
                    st.rerun()
            with confirm_col:
                if st.button("确认注销", width="stretch", type="primary", key="confirm_delete_account"):
                    if _delete_account(owner_username, confirmation):
                        _logout_session()
                        st.rerun()
                    else:
                        st.error("输入的用户名不匹配。")
        elif st.button("注销账号", width="stretch", key="sidebar_delete_account"):
            st.session_state["_confirm_delete_account"] = True
            st.rerun()
        if st.button("退出登录", width="stretch", key="sidebar_logout"):
            _logout_session()
            st.rerun()
    return str(page)


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

    if st.session_state.get("_show_display_name_editor"):
        _show_display_name_editor(owner_username, mandatory=True)
        return

    cleanup_runtime_files()
    start_workers()

    page = _render_sidebar(owner_username)
    render_brand_header()

    if st.session_state.get("_show_profile_editor"):
        _show_display_name_editor(owner_username)
        return

    if page == "作业批改":
        _show_homework_correction_page(owner_username)
    elif page == "图片增强":
        _show_image_processing_page()
    elif page == "题目识别":
        _show_paper_cut_page()
    elif page == "批改记录":
        show_records_page(owner_username)
    elif page == "学习分析":
        _show_learning_analysis_page(owner_username)
    elif page == "学习排行榜":
        _show_leaderboard_page(owner_username)
    else:
        _show_project_page()


if __name__ == "__main__":
    main()
