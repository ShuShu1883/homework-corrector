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
                subject VARCHAR(32) NULL,
                score DOUBLE NULL,
                max_score DOUBLE NULL,
                score_text VARCHAR(64) NULL,
                question_count INT NULL,
                storage_backend VARCHAR(32) NULL,
                cos_result_key VARCHAR(512) NULL,
                cos_prefix VARCHAR(512) NULL,
                error_message TEXT NULL,
                INDEX idx_results_owner_saved_at (owner_username, saved_at),
                INDEX idx_results_owner_status_saved_at (owner_username, status, saved_at)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        existing_columns = _table_columns("results")
        _ensure_column("results", "subject", "VARCHAR(32) NULL AFTER payload", existing_columns)
        _ensure_column("results", "score", "DOUBLE NULL AFTER subject", existing_columns)
        _ensure_column("results", "max_score", "DOUBLE NULL AFTER score", existing_columns)
        _ensure_column("results", "score_text", "VARCHAR(64) NULL AFTER max_score", existing_columns)
        _ensure_column("results", "question_count", "INT NULL AFTER score_text", existing_columns)
        _ensure_column("results", "storage_backend", "VARCHAR(32) NULL AFTER question_count", existing_columns)
        _ensure_column("results", "cos_result_key", "VARCHAR(512) NULL AFTER storage_backend", existing_columns)
        _ensure_column("results", "cos_prefix", "VARCHAR(512) NULL AFTER cos_result_key", existing_columns)
        _ensure_column("results", "error_message", "TEXT NULL AFTER cos_prefix", existing_columns)
        _ensure_index(
            "results",
            "idx_results_owner_status_saved_at",
            "(owner_username, status, saved_at)",
            _table_indexes("results"),
        )
        _initialized = True
        logger.info("MySQL storage initialized.")


def _table_columns(table: str) -> set[str]:
    return {str(row.get("Field")) for row in fetch_all(f"SHOW COLUMNS FROM {table}")}


def _table_indexes(table: str) -> set[str]:
    return {str(row.get("Key_name")) for row in fetch_all(f"SHOW INDEX FROM {table}")}


def _ensure_column(table: str, column: str, definition: str, existing_columns: set[str]) -> None:
    if column in existing_columns:
        return
    try:
        execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        existing_columns.add(column)
    except DatabaseError as exc:
        message = str(exc)
        if "Duplicate column" not in message and "already exists" not in message:
            raise


def _ensure_index(table: str, index_name: str, definition: str, existing_indexes: set[str]) -> None:
    if index_name in existing_indexes:
        return
    try:
        execute(f"ALTER TABLE {table} ADD INDEX {index_name} {definition}")
        existing_indexes.add(index_name)
    except DatabaseError as exc:
        message = str(exc)
        if "Duplicate key name" not in message and "already exists" not in message:
            raise


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
