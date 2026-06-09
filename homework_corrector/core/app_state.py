from __future__ import annotations

import streamlit as st


def _current_username() -> str | None:
    username = st.session_state.get("username")
    return str(username) if username else None


def _logout_session() -> None:
    st.session_state.clear()


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
