from __future__ import annotations

import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

RUNTIME_DIR = Path(os.getenv("APP_RUNTIME_DIR", BASE_DIR)).resolve()
UPLOAD_DIR = RUNTIME_DIR / "uploads"
RESULT_DIR = RUNTIME_DIR / "results"
PROCESSED_DIR = RUNTIME_DIR / "processed"
DEBUG_DIR = RUNTIME_DIR / "debug"
CUT_DIR = RUNTIME_DIR / "cuts"


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
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    CUT_DIR.mkdir(parents=True, exist_ok=True)
