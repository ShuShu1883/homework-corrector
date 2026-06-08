from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from cos_storage import (
    CosStorageError,
    delete_keys,
    delete_prefix,
    is_cos_enabled,
    list_keys,
    load_json,
    object_key,
    public_url,
    result_key as cos_result_key,
    task_prefix as cos_task_prefix,
    upload_json,
)
from config import RESULT_DIR, ensure_runtime_dirs
from db import DatabaseError, execute, fetch_all, fetch_one, initialize_database, is_mysql_enabled
from time_utils import beijing_now_iso


logger = logging.getLogger(__name__)


def _result_path(task_id: str) -> Path:
    return RESULT_DIR / f"{task_id}.json"


def _log_mysql_fallback(action: str, exc: Exception) -> None:
    logger.warning("MySQL result %s failed; falling back to JSON storage: %s", action, exc)


def _log_cos_fallback(action: str, exc: Exception) -> None:
    logger.warning("COS result %s failed; falling back to local JSON lookup: %s", action, exc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("Stored result payload is not a JSON object.")


def _save_result_json(task_id: str, result: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    payload = {
        "task_id": task_id,
        "saved_at": beijing_now_iso(),
        **result,
    }
    path = _result_path(task_id)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _save_result_mysql(task_id: str, result: dict[str, Any]) -> None:
    initialize_database()
    payload = {
        "task_id": task_id,
        "saved_at": beijing_now_iso(),
        **result,
    }
    execute(
        """
        INSERT INTO results (task_id, owner_username, status, saved_at, finished_at, payload)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            owner_username = VALUES(owner_username),
            status = VALUES(status),
            saved_at = VALUES(saved_at),
            finished_at = VALUES(finished_at),
            payload = VALUES(payload)
        """,
        (
            task_id,
            str(payload.get("owner_username") or ""),
            str(payload.get("status") or "unknown"),
            _parse_datetime(payload.get("saved_at")),
            _parse_datetime(payload.get("finished_at")),
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def _save_result_cos(task_id: str, result: dict[str, Any]) -> None:
    owner_username = str(result.get("owner_username") or "").strip().lower()
    if not owner_username:
        raise CosStorageError("COS 结果保存必须包含 owner_username。")

    key = cos_result_key(owner_username, task_id)
    payload = {
        "task_id": task_id,
        "saved_at": beijing_now_iso(),
        **result,
        "storage_backend": "cos",
        "cos_prefix": result.get("cos_prefix") or cos_task_prefix(owner_username, task_id),
        "cos_result_key": key,
        "cos_result_url": public_url(key),
    }
    upload_json(payload, key)


def save_result(task_id: str, result: dict[str, Any]) -> None:
    if is_cos_enabled():
        try:
            _save_result_cos(task_id, result)
            return
        except CosStorageError as exc:
            if result.get("status") == "failed":
                logger.warning("COS failed result save failed; saving failed state locally: %s", exc)
                _save_result_json(task_id, result)
                return
            raise

    if is_mysql_enabled():
        try:
            _save_result_mysql(task_id, result)
            return
        except DatabaseError as exc:
            _log_mysql_fallback("save", exc)

    _save_result_json(task_id, result)


def _load_result_json(task_id: str, *, owner_username: str | None = None) -> dict[str, Any] | None:
    path = _result_path(task_id)
    if not path.exists():
        return None

    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        result = {
            "task_id": task_id,
            "status": "failed",
            "error": "结果文件格式损坏，无法读取。",
        }

    if owner_username is not None and result.get("owner_username") != owner_username:
        return None
    return result


def _load_result_mysql(task_id: str, *, owner_username: str | None = None) -> dict[str, Any] | None:
    initialize_database()
    if owner_username is None:
        row = fetch_one("SELECT payload FROM results WHERE task_id = %s", (task_id,))
    else:
        row = fetch_one(
            "SELECT payload FROM results WHERE task_id = %s AND owner_username = %s",
            (task_id, owner_username),
        )
    if not row:
        return None
    return _decode_payload(row.get("payload"))


def _load_result_cos(task_id: str, *, owner_username: str | None = None) -> dict[str, Any] | None:
    if owner_username is not None:
        payload = load_json(cos_result_key(owner_username, task_id))
        if payload and payload.get("owner_username") == owner_username:
            return payload
        return None

    suffix = f"/tasks/{task_id}/result.json"
    for key in list_keys(object_key("users")):
        if not key.endswith(suffix):
            continue
        payload = load_json(key)
        if payload:
            return payload
    return None


def load_result(task_id: str, *, owner_username: str | None = None) -> dict[str, Any] | None:
    if is_cos_enabled():
        try:
            result = _load_result_cos(task_id, owner_username=owner_username)
            if result:
                return result
        except CosStorageError as exc:
            _log_cos_fallback("load", exc)
        return _load_result_json(task_id, owner_username=owner_username)

    if is_mysql_enabled():
        try:
            return _load_result_mysql(task_id, owner_username=owner_username)
        except (DatabaseError, ValueError, json.JSONDecodeError) as exc:
            _log_mysql_fallback("load", exc)

    return _load_result_json(task_id, owner_username=owner_username)


def _list_results_json(*, owner_username: str | None = None) -> list[dict[str, Any]]:
    ensure_runtime_dirs()
    results: list[dict[str, Any]] = []
    for path in sorted(RESULT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        item = _load_result_json(path.stem, owner_username=owner_username)
        if item:
            results.append(item)
    return results


def _list_results_mysql(*, owner_username: str | None = None) -> list[dict[str, Any]]:
    initialize_database()
    if owner_username is None:
        rows = fetch_all(
            "SELECT payload FROM results ORDER BY COALESCE(saved_at, finished_at) DESC, task_id DESC"
        )
    else:
        rows = fetch_all(
            """
            SELECT payload FROM results
            WHERE owner_username = %s
            ORDER BY COALESCE(saved_at, finished_at) DESC, task_id DESC
            """,
            (owner_username,),
        )
    return [_decode_payload(row.get("payload")) for row in rows]


def _list_results_cos(*, owner_username: str | None = None) -> list[dict[str, Any]]:
    if owner_username:
        prefix = object_key("users", owner_username, "tasks")
    else:
        prefix = object_key("users")

    results: list[dict[str, Any]] = []
    for key in list_keys(prefix):
        if not key.endswith("/result.json"):
            continue
        item = load_json(key)
        if not item:
            continue
        if owner_username is not None and item.get("owner_username") != owner_username:
            continue
        results.append(item)

    return sorted(
        results,
        key=lambda item: item.get("saved_at") or item.get("finished_at") or "",
        reverse=True,
    )


def _delete_results_json(owner_username: str) -> list[dict[str, Any]]:
    ensure_runtime_dirs()
    removed: list[dict[str, Any]] = []
    for path in sorted(RESULT_DIR.glob("*.json")):
        item = _load_result_json(path.stem, owner_username=owner_username)
        if not item:
            continue
        removed.append(item)
        path.unlink(missing_ok=True)
        path.with_suffix(".tmp").unlink(missing_ok=True)
    return removed


def _delete_results_cos(owner_username: str) -> list[dict[str, Any]]:
    removed = _list_results_cos(owner_username=owner_username)
    keys_to_delete: set[str] = set()
    prefixes_to_delete: set[str] = set()
    for item in removed:
        prefix = item.get("cos_prefix")
        if prefix:
            prefixes_to_delete.add(str(prefix))
        result_key = item.get("cos_result_key")
        if result_key:
            keys_to_delete.add(str(result_key))
        for obj in item.get("cos_objects", []):
            if isinstance(obj, dict) and obj.get("key"):
                keys_to_delete.add(str(obj["key"]))

    for prefix in sorted(prefixes_to_delete):
        try:
            delete_prefix(prefix)
        except CosStorageError as exc:
            logger.warning("COS prefix delete failed for %s: %s", prefix, exc)

    if keys_to_delete:
        try:
            delete_keys(keys_to_delete)
        except CosStorageError as exc:
            logger.warning("COS object delete failed for %s: %s", owner_username, exc)
    return removed


def _delete_results_mysql(owner_username: str) -> list[dict[str, Any]]:
    initialize_database()
    rows = fetch_all("SELECT payload FROM results WHERE owner_username = %s", (owner_username,))
    removed = [_decode_payload(row.get("payload")) for row in rows]
    execute("DELETE FROM results WHERE owner_username = %s", (owner_username,))
    return removed


def list_results(*, owner_username: str | None = None) -> list[dict[str, Any]]:
    if is_cos_enabled():
        merged: dict[str, dict[str, Any]] = {}
        try:
            for item in _list_results_cos(owner_username=owner_username):
                task_id = str(item.get("task_id") or "")
                if task_id:
                    merged[task_id] = item
        except (CosStorageError, ValueError, json.JSONDecodeError) as exc:
            _log_cos_fallback("list", exc)

        for item in _list_results_json(owner_username=owner_username):
            task_id = str(item.get("task_id") or "")
            if task_id and task_id not in merged:
                merged[task_id] = item

        return sorted(
            merged.values(),
            key=lambda item: item.get("saved_at") or item.get("finished_at") or "",
            reverse=True,
        )

    if is_mysql_enabled():
        try:
            return _list_results_mysql(owner_username=owner_username)
        except (DatabaseError, ValueError, json.JSONDecodeError) as exc:
            _log_mysql_fallback("list", exc)

    return _list_results_json(owner_username=owner_username)


def delete_results(owner_username: str) -> list[dict[str, Any]]:
    normalized = str(owner_username or "").strip().lower()
    if not normalized:
        return []

    if is_cos_enabled():
        removed: list[dict[str, Any]] = []
        try:
            removed.extend(_delete_results_cos(normalized))
        except (CosStorageError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("COS result delete failed for %s: %s", normalized, exc)
        removed.extend(_delete_results_json(normalized))
        return removed

    if is_mysql_enabled():
        try:
            return _delete_results_mysql(normalized)
        except (DatabaseError, ValueError, json.JSONDecodeError) as exc:
            _log_mysql_fallback("delete", exc)

    return _delete_results_json(normalized)
