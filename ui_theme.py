from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st


_STATUS_CLASSES = {
    "waiting": "waiting",
    "running": "running",
    "finished": "finished",
    "failed": "failed",
    "unknown": "unknown",
}


def escape_html(value: Any) -> str:
    return escape(str(value if value not in (None, "") else "-"), quote=True)


def task_card_button_key(task_id: str) -> str:
    return f"task_card_view_{task_id}"


def status_badge_html(status: str, label: str) -> str:
    css_class = _STATUS_CLASSES.get(str(status), "unknown")
    return f'<span class="status-pill status-{css_class}">{escape_html(label)}</span>'


def build_task_card_html(row: dict[str, Any]) -> str:
    status = str(row.get("_status") or "unknown")
    badge = status_badge_html(status, str(row.get("状态") or status))
    return f"""
    <div class="task-card-content">
        <div class="task-card-top">
            <div>
                <div class="task-card-label">作业批改任务</div>
                <div class="task-card-id">{escape_html(row.get("任务ID"))}</div>
            </div>
            {badge}
        </div>
        <div class="task-card-grid">
            <div><span>得分</span><strong>{escape_html(row.get("分数"))}</strong></div>
            <div><span>创建时间</span><strong>{escape_html(row.get("创建时间"))}</strong></div>
            <div><span>更新时间</span><strong>{escape_html(row.get("更新时间"))}</strong></div>
        </div>
    </div>
    """


def build_leaderboard_card_html(row: dict[str, Any], *, is_current_user: bool = False) -> str:
    rank = int(row.get("rank") or 0)
    rank_label = "👑" if rank == 1 else str(rank)
    current_class = " current-user" if is_current_user else ""
    me_badge = '<span class="leaderboard-me">我</span>' if is_current_user else ""
    return f"""
    <div class="leaderboard-card{current_class}">
        <div class="leaderboard-avatar">{escape_html(rank_label)}</div>
        <div class="leaderboard-person">
            <div class="leaderboard-name">{escape_html(row.get("display_name"))}{me_badge}</div>
            <div class="leaderboard-username">{escape_html(row.get("username"))}</div>
        </div>
        <div class="leaderboard-stats">
            <div>
                <strong>{escape_html(row.get("average_rate_label"))}</strong>
                <span>平均得分率</span>
            </div>
            <div>
                <strong>{escape_html(row.get("task_count"))}</strong>
                <span>批改次数</span>
            </div>
            <div>
                <strong>{escape_html(row.get("total_score_label"))}</strong>
                <span>总分</span>
            </div>
        </div>
    </div>
    """


def apply_app_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --sakura-primary: #d94f91;
            --sakura-deep: #9d3f73;
            --sakura-soft: #fff0f7;
            --sakura-card: rgba(255, 255, 255, 0.86);
            --sakura-border: rgba(224, 125, 173, 0.24);
            --sakura-shadow: 0 14px 34px rgba(183, 86, 137, 0.10);
            --sakura-text: #57364b;
            --sakura-muted: #8d7283;
        }

        .stApp {
            background:
                radial-gradient(circle at 8% 8%, rgba(255, 199, 225, 0.42), transparent 24rem),
                radial-gradient(circle at 92% 16%, rgba(255, 228, 241, 0.78), transparent 26rem),
                linear-gradient(145deg, #fffafd 0%, #fff5fa 48%, #fffafd 100%);
            color: var(--sakura-text);
        }

        [data-testid="stAppViewContainer"] > .main .block-container {
            max-width: 1320px;
            padding-top: 2.2rem;
            padding-bottom: 3rem;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #fff7fb 0%, #ffedf6 100%);
            border-right: 1px solid var(--sakura-border);
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
            color: var(--sakura-text);
        }

        h1, h2, h3, h4 {
            color: #633b56;
            letter-spacing: -0.02em;
        }

        [data-testid="stForm"],
        [data-testid="stFileUploader"],
        [data-testid="stExpander"],
        [data-testid="stMetric"],
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--sakura-card);
            border: 1px solid var(--sakura-border);
            border-radius: 20px;
            box-shadow: var(--sakura-shadow);
        }

        [data-testid="stForm"] {
            padding: 1rem;
        }

        [data-testid="stMetric"] {
            padding: 1rem 1.1rem;
        }

        [data-testid="stMetricValue"] {
            color: var(--sakura-deep);
        }

        [data-testid="stFileUploader"] {
            padding: 0.55rem;
        }

        .stButton > button,
        .stDownloadButton > button,
        [data-testid="stFormSubmitButton"] > button {
            border-radius: 999px;
            border-color: rgba(217, 79, 145, 0.32);
            transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover,
        [data-testid="stFormSubmitButton"] > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 8px 18px rgba(193, 81, 140, 0.16);
            border-color: rgba(217, 79, 145, 0.62);
        }

        div[data-baseweb="tab-list"] {
            gap: 0.45rem;
        }

        button[data-baseweb="tab"] {
            border-radius: 999px;
            padding-left: 1.1rem;
            padding-right: 1.1rem;
        }

        .anime-hero {
            min-height: 33rem;
            display: flex;
            flex-direction: column;
            justify-content: center;
            padding: 3.4rem 3rem;
            border-radius: 30px;
            background:
                radial-gradient(circle at 86% 10%, rgba(255,255,255,.92) 0 3px, transparent 4px),
                radial-gradient(circle at 77% 22%, rgba(255,255,255,.78) 0 5px, transparent 6px),
                linear-gradient(145deg, rgba(255, 220, 237, .95), rgba(255, 242, 249, .94));
            border: 1px solid rgba(226, 127, 177, 0.24);
            box-shadow: 0 22px 60px rgba(195, 94, 148, .15);
        }

        .anime-kicker,
        .page-kicker,
        .task-card-label {
            color: var(--sakura-primary);
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.14em;
            text-transform: uppercase;
        }

        .anime-hero h1 {
            margin: .45rem 0 .8rem;
            color: #703d5b;
            font-size: clamp(2.2rem, 5vw, 4.1rem);
            line-height: 1.08;
        }

        .anime-hero p,
        .page-intro p,
        .brand-copy,
        .task-card-grid span {
            color: var(--sakura-muted);
        }

        .anime-tags {
            display: flex;
            flex-wrap: wrap;
            gap: .65rem;
            margin-top: 1.6rem;
        }

        .anime-tag,
        .status-pill {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: .4rem .75rem;
            font-size: .82rem;
            font-weight: 700;
        }

        .anime-tag {
            color: #985277;
            background: rgba(255,255,255,.72);
            border: 1px solid rgba(213, 110, 165, .2);
        }

        .login-heading {
            margin: 3rem 0 1.1rem;
        }

        .login-heading h2 {
            margin: 0 0 .35rem;
        }

        .brand-header,
        .page-intro {
            border-radius: 24px;
            border: 1px solid var(--sakura-border);
            background: rgba(255,255,255,.68);
            box-shadow: var(--sakura-shadow);
        }

        .brand-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            padding: 1.2rem 1.45rem;
            margin-bottom: 1rem;
        }

        .brand-title {
            color: #703d5b;
            font-size: 1.45rem;
            font-weight: 850;
        }

        .brand-mark {
            color: var(--sakura-primary);
            font-size: 1.35rem;
            letter-spacing: .22em;
            white-space: nowrap;
        }

        .page-intro {
            padding: 1.25rem 1.4rem;
            margin: .35rem 0 1rem;
        }

        .page-intro h2 {
            margin: .12rem 0 .22rem;
            font-size: 1.65rem;
        }

        .page-intro p {
            margin: 0;
        }

        .steps-row {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: .7rem;
            margin: 0 0 1rem;
        }

        .step-chip {
            padding: .75rem .85rem;
            border-radius: 16px;
            color: #80506a;
            background: rgba(255,255,255,.70);
            border: 1px solid rgba(224, 125, 173, .22);
            font-size: .88rem;
        }

        .step-chip strong {
            color: var(--sakura-primary);
            margin-right: .35rem;
        }

        .sidebar-brand {
            padding: .9rem .15rem .5rem;
            color: #713f5b;
            font-size: 1.08rem;
            font-weight: 850;
        }

        .sidebar-account {
            margin: .15rem 0 .85rem;
            padding: .75rem .85rem;
            border-radius: 16px;
            color: #85536e;
            background: rgba(255,255,255,.68);
            border: 1px solid var(--sakura-border);
            font-size: .88rem;
        }

        .task-card-content {
            padding: .2rem .15rem .35rem;
        }

        .task-card-top,
        .task-card-grid {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
        }

        .task-card-id {
            margin-top: .28rem;
            color: #704159;
            font-weight: 750;
            word-break: break-all;
        }

        .task-card-grid {
            margin-top: .9rem;
            padding-top: .75rem;
            border-top: 1px solid rgba(224, 125, 173, .16);
        }

        .task-card-grid div {
            display: flex;
            flex-direction: column;
            gap: .12rem;
        }

        .task-card-grid strong {
            color: #704159;
            font-size: .93rem;
        }

        .leaderboard-card {
            display: grid;
            grid-template-columns: auto minmax(0, 1fr) auto;
            align-items: center;
            gap: 1.15rem;
            min-height: 6.4rem;
            padding: 1.05rem 1.6rem;
            margin: .8rem 0;
            border-radius: 24px;
            border: 1px solid rgba(226, 127, 177, .46);
            background: rgba(255, 246, 251, .74);
            box-shadow: 0 8px 22px rgba(217, 79, 145, .10), inset 0 0 0 3px rgba(255, 202, 226, .22);
        }

        .leaderboard-card.current-user {
            border-color: rgba(217, 79, 145, .72);
            background: rgba(255, 241, 248, .90);
        }

        .leaderboard-avatar {
            width: 3.7rem;
            height: 3.7rem;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            color: #a23c75;
            background: linear-gradient(145deg, #ffd75d, #ffc94d);
            font-size: 1.18rem;
            font-weight: 900;
            box-shadow: 0 8px 18px rgba(255, 198, 68, .28);
        }

        .leaderboard-person {
            min-width: 0;
        }

        .leaderboard-name {
            display: flex;
            align-items: center;
            gap: .45rem;
            color: #60354f;
            font-size: 1.08rem;
            font-weight: 850;
            line-height: 1.2;
        }

        .leaderboard-username {
            margin-top: .4rem;
            color: var(--sakura-muted);
            font-size: .92rem;
            word-break: break-all;
        }

        .leaderboard-me {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 1.75rem;
            height: 1.75rem;
            padding: 0 .45rem;
            border-radius: 999px;
            color: white;
            background: var(--sakura-primary);
            font-size: .82rem;
            font-weight: 850;
        }

        .leaderboard-stats {
            display: grid;
            grid-template-columns: repeat(3, minmax(5.2rem, 1fr));
            gap: 1.1rem;
            min-width: 24rem;
            text-align: center;
        }

        .leaderboard-stats div {
            display: flex;
            flex-direction: column;
            gap: .28rem;
        }

        .leaderboard-stats strong {
            color: #a23c75;
            font-size: 1.05rem;
            font-weight: 900;
        }

        .leaderboard-stats span {
            color: var(--sakura-muted);
            font-size: .88rem;
        }

        .status-finished { color: #28745b; background: #e7f8f0; }
        .status-running { color: #956322; background: #fff5d9; }
        .status-waiting { color: #8d5b85; background: #f8eafb; }
        .status-failed { color: #a9495e; background: #ffe8ed; }
        .status-unknown { color: #73707b; background: #f0eef2; }

        @media (max-width: 800px) {
            [data-testid="stAppViewContainer"] > .main .block-container {
                padding-top: 1.25rem;
            }

            .anime-hero {
                min-height: auto;
                padding: 2rem 1.35rem;
            }

            .login-heading {
                margin-top: .8rem;
            }

            .brand-header,
            .task-card-top,
            .task-card-grid,
            .leaderboard-card {
                flex-direction: column;
            }

            .leaderboard-card {
                display: flex;
                align-items: flex-start;
                padding: 1rem;
            }

            .leaderboard-stats {
                width: 100%;
                min-width: 0;
                grid-template-columns: repeat(3, 1fr);
                gap: .55rem;
            }

            .steps-row {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_auth_hero() -> None:
    st.markdown(
        """
        <section class="anime-hero">
            <div class="anime-kicker">Sakura Homework Studio ✦</div>
            <h1>让每一次批改<br>都更轻松一点</h1>
            <p>上传作业图片，交给 OCR 与大模型完成切题、分析和逐题反馈。适合日常练习，也适合课堂使用。</p>
            <div class="anime-tags">
                <span class="anime-tag">✦ 智能切题</span>
                <span class="anime-tag">✦ 逐题批改</span>
                <span class="anime-tag">✦ 学习建议</span>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_login_heading() -> None:
    st.markdown(
        """
        <div class="login-heading">
            <div class="page-kicker">Welcome back ✦</div>
            <h2>登录后开始批改</h2>
            <p class="brand-copy">登录状态仅保存在当前页面会话中，刷新网页后需要重新登录。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_brand_header() -> None:
    st.markdown(
        """
        <header class="brand-header">
            <div>
                <div class="brand-title">中小学作业智能批改系统</div>
                <div class="brand-copy">任务队列 · 腾讯云切题 OCR · 大模型批改</div>
            </div>
            <div class="brand-mark">✦ ✧ ✦</div>
        </header>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_identity(username: str, display_name: str | None = None) -> None:
    display_name = display_name or username
    st.markdown(
        f"""
        <div class="sidebar-brand">✦ Sakura 批改台</div>
        <div class="sidebar-account">用户名<br><strong>{escape_html(display_name)}</strong><br><span>账号：{escape_html(username)}</span></div>
        """,
        unsafe_allow_html=True,
    )


def render_page_intro(title: str, description: str, *, kicker: str = "Sakura workspace ✦") -> None:
    st.markdown(
        f"""
        <section class="page-intro">
            <div class="page-kicker">{escape_html(kicker)}</div>
            <h2>{escape_html(title)}</h2>
            <p>{escape_html(description)}</p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def render_steps(steps: list[str]) -> None:
    chips = "".join(
        f'<div class="step-chip"><strong>{index:02d}</strong>{escape_html(step)}</div>'
        for index, step in enumerate(steps, start=1)
    )
    st.markdown(f'<div class="steps-row">{chips}</div>', unsafe_allow_html=True)
