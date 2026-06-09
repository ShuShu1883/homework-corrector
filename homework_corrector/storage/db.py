from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from homework_corrector.core.config import get_int_setting, get_setting


logger = logging.getLogger(__name__)
_init_lock = threading.RLock()
_initialized = False


class DatabaseError(RuntimeError):
    pass


def is_mysql_enabled() -> bool:
    return (get_setting("DB_BACKEND", "json") or "json").strip().lower() == "mysql"


def _mysql_ssl_enabled() -> bool:
    raw = (get_setting("MYSQL_SSL", "false") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _connection_kwargs() -> dict[str, Any]:
    password = get_setting("MYSQL_PASSWORD")
    config = {
        "host": get_setting("MYSQL_HOST", ""),
        "port": get_int_setting("MYSQL_PORT", 3306),
        "user": get_setting("MYSQL_USER", ""),
        "password": password or "",
        "database": get_setting("MYSQL_DATABASE", ""),
    }
    missing = [name for name, value in config.items() if name != "password" and not value]
    if not password:
        missing.append("password")
    if missing:
        raise DatabaseError(f"MySQL configuration is incomplete: {', '.join(missing)}")
    return config


@contextmanager
def connect() -> Iterator[Any]:
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as exc:
        raise DatabaseError("PyMySQL is not installed.") from exc

    kwargs = _connection_kwargs()
    if _mysql_ssl_enabled():
        kwargs["ssl"] = {}

    try:
        connection = pymysql.connect(
            **kwargs,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
        )
    except Exception as exc:
        raise DatabaseError(f"Could not connect to MySQL: {exc}") from exc

    try:
        yield connection
    finally:
        connection.close()


def initialize_database() -> None:
    global _initialized

    if _initialized:
        return

    with _init_lock:
        if _initialized:
            return

        execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR(24) PRIMARY KEY,
                password VARCHAR(128) NOT NULL,
                display_name VARCHAR(80) NULL,
                created_at DATETIME NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        try:
            execute("ALTER TABLE users ADD COLUMN display_name VARCHAR(80) NULL AFTER password")
        except DatabaseError as exc:
            if "Duplicate column" not in str(exc) and "already exists" not in str(exc):
                raise
        execute(
            """
            CREATE TABLE IF NOT EXISTS results (
                task_id VARCHAR(64) PRIMARY KEY,
                owner_username VARCHAR(24) NOT NULL,
                status VARCHAR(32) NOT NULL,
                saved_at DATETIME NULL,
                finished_at DATETIME NULL,
                payload JSON NOT NULL,
                INDEX idx_results_owner_saved_at (owner_username, saved_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        _initialized = True
        logger.info("MySQL storage initialized.")


def execute(sql: str, args: Any | None = None) -> None:
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, args)
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(f"MySQL execute failed: {exc}") from exc


def fetch_one(sql: str, args: Any | None = None) -> dict[str, Any] | None:
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, args)
                return cursor.fetchone()
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(f"MySQL fetch failed: {exc}") from exc


def fetch_all(sql: str, args: Any | None = None) -> list[dict[str, Any]]:
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, args)
                return list(cursor.fetchall())
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(f"MySQL fetch failed: {exc}") from exc
