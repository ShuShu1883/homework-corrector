from __future__ import annotations

from pathlib import Path
from typing import Any

from homework_corrector.core.config import CUT_DIR, PROCESSED_DIR, UPLOAD_DIR
from homework_corrector.storage.cos_storage import is_cos_enabled, task_file_key, task_prefix, upload_file
from homework_corrector.core.resource_paths import is_http_url, is_local_file, safe_unlink


TOP_LEVEL_PATH_KEYS = (
    "image_path",
    "original_path",
    "warped_path",
    "enhanced_path",
    "enhanced_preview_path",
    "image_preview_path",
    "source_image_path",
    "raw_path",
    "original_image_path",
    "original_preview_path",
    "api_preview_path",
    "enhanced_image_path",
    "ocr_image_path",
    "ocr_preview_path",
    "annotated_image_path",
)
API_META_PATH_KEYS = ("source_image_path", "api_image_path")


def _path_category(path_value: str, default: str) -> str:
    name = Path(path_value).parent.name.lower()
    if name in {"uploads", "processed", "cuts"}:
        return name
    return default


def _upload_path(
    value: Any,
    *,
    owner_username: str,
    task_id: str,
    default_category: str,
    uploaded_by_path: dict[str, dict[str, Any]],
    cos_objects: list[dict[str, Any]],
    local_paths: set[str],
) -> str:
    if is_http_url(value) or not is_local_file(value):
        return str(value) if value not in (None, "") else ""

    path = str(Path(str(value)).resolve())
    existing = uploaded_by_path.get(path)
    if existing:
        return str(existing["url"])

    category = _path_category(path, default_category)
    key = task_file_key(owner_username, task_id, category, Path(path).name)
    uploaded = upload_file(path, key)
    uploaded_by_path[path] = uploaded
    local_paths.add(path)
    cos_objects.append(
        {
            "key": uploaded["key"],
            "url": uploaded["url"],
            "size": uploaded["size"],
            "content_type": uploaded["content_type"],
        }
    )
    return str(uploaded["url"])


def upload_result_assets(result: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    if not is_cos_enabled():
        return result, set()

    owner_username = str(result.get("owner_username") or "").strip().lower()
    task_id = str(result.get("task_id") or "").strip()
    if not owner_username or not task_id:
        return result, set()

    uploaded_by_path: dict[str, dict[str, Any]] = {}
    cos_objects: list[dict[str, Any]] = []
    local_paths: set[str] = set()

    for key in TOP_LEVEL_PATH_KEYS:
        if key in result:
            result[key] = _upload_path(
                result.get(key),
                owner_username=owner_username,
                task_id=task_id,
                default_category="images",
                uploaded_by_path=uploaded_by_path,
                cos_objects=cos_objects,
                local_paths=local_paths,
            )

    processing = result.get("processing")
    if isinstance(processing, dict):
        for key, value in list(processing.items()):
            if isinstance(value, str):
                processing[key] = _upload_path(
                    value,
                    owner_username=owner_username,
                    task_id=task_id,
                    default_category="processing",
                    uploaded_by_path=uploaded_by_path,
                    cos_objects=cos_objects,
                    local_paths=local_paths,
                )

    api_meta = result.get("api_image_meta")
    if isinstance(api_meta, dict):
        for key in API_META_PATH_KEYS:
            if key in api_meta:
                api_meta[key] = _upload_path(
                    api_meta.get(key),
                    owner_username=owner_username,
                    task_id=task_id,
                    default_category="processed",
                    uploaded_by_path=uploaded_by_path,
                    cos_objects=cos_objects,
                    local_paths=local_paths,
                )

    question_groups = [result.get("paper_cut_questions"), result.get("questions")]
    for questions in question_groups:
        if not isinstance(questions, list):
            continue
        for item in questions:
            if isinstance(item, dict) and item.get("crop_path"):
                item["crop_path"] = _upload_path(
                    item.get("crop_path"),
                    owner_username=owner_username,
                    task_id=task_id,
                    default_category="cuts",
                    uploaded_by_path=uploaded_by_path,
                    cos_objects=cos_objects,
                    local_paths=local_paths,
                )

    existing_objects = result.get("cos_objects")
    if not isinstance(existing_objects, list):
        existing_objects = []
    result["cos_objects"] = existing_objects + cos_objects
    result["cos_prefix"] = task_prefix(owner_username, task_id)
    result["storage_backend"] = "cos"
    return result, local_paths


def delete_local_files(paths: set[str]) -> None:
    for path in sorted(paths):
        safe_unlink(path)


def delete_task_local_files(task_id: str) -> None:
    needle = str(task_id or "").strip()
    if not needle:
        return
    for directory in (UPLOAD_DIR, PROCESSED_DIR, CUT_DIR):
        if not directory.exists():
            continue
        for path in directory.rglob(f"*{needle}*"):
            if path.is_file() and path.name != ".gitkeep":
                safe_unlink(path)
