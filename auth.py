from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from config import DATA_DIR, ensure_runtime_dirs
from db import DatabaseError, execute, fetch_one, initialize_database, is_mysql_enabled
from time_utils import beijing_now, beijing_now_iso


USERS_PATH = DATA_DIR / "users.json"
USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,24}$")
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128
_users_lock = threading.RLock()
logger = logging.getLogger(__name__)


class AuthValidationError(ValueError):
    pass


def normalize_username(username: str) -> str:
    normalized = str(username or "").strip().lower()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise AuthValidationError("用户名只能包含 3-24 位小写字母、数字或下划线。")
    return normalized


def _validate_password(password: str) -> str:
    value = str(password or "")
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH:
        raise AuthValidationError("密码长度必须为 6-128 位。")
    return value


def _load_users() -> list[dict[str, Any]]:
    if not USERS_PATH.exists():
        return []

    try:
        payload = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("账号文件格式损坏，无法读取。") from exc

    users = payload.get("users")
    if not isinstance(users, list):
        raise RuntimeError("账号文件格式损坏，缺少 users 列表。")
    return [item for item in users if isinstance(item, dict)]


def _save_users(users: list[dict[str, Any]]) -> None:
    ensure_runtime_dirs()
    temp_path = USERS_PATH.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps({"users": users}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(USERS_PATH)


def _log_mysql_fallback(action: str, exc: Exception) -> None:
    logger.warning("MySQL auth %s failed; falling back to JSON storage: %s", action, exc)


def _mysql_register_user(username: str, password: str) -> str:
    initialize_database()
    existing = fetch_one("SELECT username FROM users WHERE username = %s", (username,))
    if existing:
        raise AuthValidationError("用户名已存在，请换一个用户名。")

    try:
        execute(
            "INSERT INTO users (username, password, created_at) VALUES (%s, %s, %s)",
            (username, password, beijing_now()),
        )
    except DatabaseError as exc:
        if "Duplicate entry" in str(exc):
            raise AuthValidationError("用户名已存在，请换一个用户名。") from exc
        raise
    return username


def _mysql_authenticate_user(username: str, password: str) -> str | None:
    initialize_database()
    row = fetch_one("SELECT username, password FROM users WHERE username = %s", (username,))
    if row and row.get("password") == password:
        return str(row.get("username"))
    return None


def register_user(username: str, password: str) -> str:
    normalized = normalize_username(username)
    validated_password = _validate_password(password)

    if is_mysql_enabled():
        try:
            return _mysql_register_user(normalized, validated_password)
        except AuthValidationError:
            raise
        except DatabaseError as exc:
            _log_mysql_fallback("registration", exc)

    with _users_lock:
        users = _load_users()
        if any(item.get("username") == normalized for item in users):
            raise AuthValidationError("用户名已存在，请换一个用户名。")

        users.append(
            {
                "username": normalized,
                "password": validated_password,
                "created_at": beijing_now_iso(),
            }
        )
        _save_users(users)
    return normalized


def authenticate_user(username: str, password: str) -> str | None:
    try:
        normalized = normalize_username(username)
    except AuthValidationError:
        return None

    validated_password = str(password or "")
    if is_mysql_enabled():
        try:
            return _mysql_authenticate_user(normalized, validated_password)
        except DatabaseError as exc:
            _log_mysql_fallback("authentication", exc)

    with _users_lock:
        users = _load_users()
        for item in users:
            if item.get("username") == normalized and item.get("password") == validated_password:
                return normalized
    return None
