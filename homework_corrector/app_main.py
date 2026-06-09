from __future__ import annotations

import streamlit as st

from homework_corrector.ui.account_pages import (
    _current_display_name,
    _delete_account,
    _show_auth_page,
    _show_display_name_editor,
)
from homework_corrector.ui.analysis_pages import _show_leaderboard_page, _show_learning_analysis_page
from homework_corrector.core.app_state import _current_username, _logout_session, _query_param
from homework_corrector.core.config import ensure_runtime_dirs
from homework_corrector.ui.image_inputs import (
    _select_image_inputs,
    _show_mobile_capture_page,
    _uploaded_files_signature,
    _uploaded_preview,
)
from homework_corrector.ui.result_views import _show_result, show_records_page
from homework_corrector.processing.score_utils import STATUS_LABELS, _score_display
from homework_corrector.storage.storage import load_result
from homework_corrector.tasks.task_queue import get_task_status, start_workers, submit_tasks
from homework_corrector.ui.tool_pages import _show_image_processing_page, _show_paper_cut_page
from homework_corrector.ui.ui_theme import (
    apply_app_theme,
    render_brand_header,
    render_page_intro,
    render_sidebar_identity,
    render_steps,
)


PAGES = ["作业批改", "图片增强", "题目识别", "批改记录", "学习分析", "学习排行榜"]
POLL_INTERVAL = "3s"
ACTIVE_TASK_STATUSES = {"waiting", "running"}


def _batch_task_rows(task_ids: list[str], owner_username: str) -> tuple[list[dict[str, str]], bool]:
    rows: list[dict[str, str]] = []
    has_active_task = False
    for index, task_id in enumerate(task_ids, start=1):
        status = get_task_status(task_id, owner_username=owner_username)
        result = load_result(task_id, owner_username=owner_username)
        raw_status = str(status.get("status") or "unknown")
        has_active_task = has_active_task or raw_status in ACTIVE_TASK_STATUSES
        rows.append(
            {
                "index": str(index),
                "task_id": task_id,
                "status": STATUS_LABELS.get(raw_status, raw_status),
                "score": _score_display(result.get("questions", [])) if result else "-",
            }
        )
    return rows, has_active_task


def _render_batch_task_rows(rows: list[dict[str, str]]) -> None:
    st.markdown("#### 本次批量任务")
    for row in rows:
        task_id = row["task_id"]
        with st.container(border=True):
            cols = st.columns([0.8, 1.2, 1.2, 2.4, 1.2])
            cols[0].metric("序号", row["index"])
            cols[1].metric("状态", row["status"])
            cols[2].metric("分数", row["score"])
            cols[3].caption(f"任务ID：{task_id}")
            if cols[4].button("查看详情", key=f"batch_task_view_{task_id}", width="stretch"):
                st.session_state["selected_task_id"] = task_id
                st.rerun()


@st.fragment(run_every=POLL_INTERVAL)
def _poll_batch_task_list(task_ids: list[str], owner_username: str) -> None:
    rows, has_active_task = _batch_task_rows(task_ids, owner_username)
    _render_batch_task_rows(rows)
    if not has_active_task:
        st.rerun()


def _show_batch_task_list(task_ids: list[str], owner_username: str) -> None:
    if not task_ids:
        return

    rows, has_active_task = _batch_task_rows(task_ids, owner_username)
    if has_active_task:
        _poll_batch_task_list(task_ids, owner_username)
    else:
        _render_batch_task_rows(rows)


def _show_homework_correction_page(owner_username: str) -> None:
    render_page_intro(
        "作业批改",
        "一次提交一张或多张作业图片，后台队列会为每张图片生成独立批改报告。",
        kicker="Homework correction ✦",
    )
    render_steps(["上传作业图片", "提交批改任务", "查看本次报告"])
    uploaded_files, image_source = _select_image_inputs(
        key_prefix="correction",
        uploader_label="上传作业图片",
        camera_label="拍摄作业图片",
        owner_username=owner_username,
    )
    if uploaded_files:
        file_signature = _uploaded_files_signature(uploaded_files, source=image_source or "upload")
        if st.session_state.get("correction_file_signature") != file_signature:
            st.session_state["correction_file_signature"] = file_signature
            st.session_state.pop("selected_task_id", None)
            st.session_state.pop("selected_task_ids", None)

        st.markdown("#### 待批改作业")
        preview_files = uploaded_files[:6]
        preview_cols = st.columns(min(3, len(preview_files)))
        for index, uploaded_file in enumerate(preview_files, start=1):
            with preview_cols[(index - 1) % len(preview_cols)]:
                st.image(_uploaded_preview(uploaded_file), caption=f"第 {index} 张", width="stretch")
        if len(uploaded_files) > len(preview_files):
            st.caption(f"已选择 {len(uploaded_files)} 张图片，当前预览前 {len(preview_files)} 张。")

    if st.button(
        "提交批量批改任务",
        type="primary",
        disabled=not uploaded_files,
        key="submit_correction_task",
    ):
        task_ids = submit_tasks(uploaded_files, owner_username)
        st.session_state["selected_task_ids"] = task_ids
        st.session_state.pop("selected_task_id", None)
        st.success(f"已提交 {len(task_ids)} 个批改任务。")

    selected_task_ids = [
        str(task_id)
        for task_id in st.session_state.get("selected_task_ids", [])
        if str(task_id).strip()
    ]
    if selected_task_ids:
        st.divider()
        _show_batch_task_list(selected_task_ids, owner_username)

    selected_task_id = st.session_state.get("selected_task_id")
    if selected_task_id:
        st.divider()
        _show_result(selected_task_id, owner_username)


def _render_sidebar(owner_username: str) -> str:
    with st.sidebar:
        render_sidebar_identity(owner_username, _current_display_name(owner_username))
        if st.session_state.get("main_page") not in PAGES:
            st.session_state["main_page"] = PAGES[0]
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
        _show_homework_correction_page(owner_username)


if __name__ == "__main__":
    main()
