from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"


def _from_streamlit_secrets(name: str) -> Any | None:
    try:
        import streamlit as st

        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        return None
    return None


def get_setting(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value not in (None, ""):
        return value

    secret_value = _from_streamlit_secrets(name)
    if secret_value not in (None, ""):
        return str(secret_value)

    return default


def get_int_setting(name: str, default: int) -> int:
    raw = get_setting(name)
    if raw is None:
        return default

    try:
        return int(raw)
    except ValueError:
        return default


def ensure_runtime_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
