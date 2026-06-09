from __future__ import annotations

import json
import mimetypes
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote, urlparse

from homework_corrector.core.config import get_setting


DEFAULT_COS_PREFIX = "homework-correction"
_client_lock = threading.RLock()
_cached_client: Any | None = None
_cached_settings: "CosSettings | None" = None


class CosStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class CosSettings:
    secret_id: str
    secret_key: str
    region: str
    bucket: str
    prefix: str
    public_base_url: str


def is_cos_enabled() -> bool:
    backend = (get_setting("FILE_STORAGE_BACKEND", "json") or "json").strip().lower()
    return backend == "cos"


def _clean_prefix(value: str | None) -> str:
    raw = (value or DEFAULT_COS_PREFIX).strip().strip("/")
    return "/".join(part for part in raw.split("/") if part)


def _required_setting(name: str) -> str:
    value = (get_setting(name) or "").strip()
    if not value:
        raise CosStorageError(f"未配置 {name}，无法使用腾讯云 COS 存储。")
    return value


def get_cos_settings() -> CosSettings:
    secret_id = _required_setting("COS_SECRET_ID")
    secret_key = _required_setting("COS_SECRET_KEY")
    region = _required_setting("COS_REGION")
    bucket = _required_setting("COS_BUCKET")
    prefix = _clean_prefix(get_setting("COS_PREFIX", DEFAULT_COS_PREFIX))
    public_base_url = (get_setting("COS_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not public_base_url:
        public_base_url = f"https://{bucket}.cos.{region}.myqcloud.com"
    return CosSettings(secret_id, secret_key, region, bucket, prefix, public_base_url)


def _client() -> Any:
    global _cached_client, _cached_settings

    settings = get_cos_settings()
    with _client_lock:
        if _cached_client is not None and _cached_settings == settings:
            return _cached_client

        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError as exc:
            raise CosStorageError("未安装腾讯云 COS SDK，请先安装 cos-python-sdk-v5。") from exc

        config = CosConfig(
            Region=settings.region,
            SecretId=settings.secret_id,
            SecretKey=settings.secret_key,
            Scheme="https",
        )
        _cached_client = CosS3Client(config)
        _cached_settings = settings
        return _cached_client


def _safe_segment(value: Any) -> str:
    segment = str(value or "").strip()
    segment = re.sub(r"[^A-Za-z0-9_.=-]+", "_", segment)
    return segment.strip("._") or "unknown"


def object_key(*parts: Any) -> str:
    settings = get_cos_settings()
    cleaned_parts = [settings.prefix] if settings.prefix else []
    cleaned_parts.extend(str(part).strip("/") for part in parts if str(part or "").strip("/"))
    return "/".join(cleaned_parts)


def task_prefix(owner_username: str, task_id: str) -> str:
    return object_key("users", _safe_segment(owner_username), "tasks", _safe_segment(task_id))


def task_file_key(owner_username: str, task_id: str, category: str, filename: str) -> str:
    return f"{task_prefix(owner_username, task_id)}/{_safe_segment(category)}/{_safe_segment(filename)}"


def result_key(owner_username: str, task_id: str) -> str:
    return f"{task_prefix(owner_username, task_id)}/result.json"


def public_url(key: str) -> str:
    settings = get_cos_settings()
    return f"{settings.public_base_url}/{quote(key, safe='/')}"


def public_url_to_key(url: str) -> str | None:
    settings = get_cos_settings()
    base = settings.public_base_url.rstrip("/")
    if not str(url or "").startswith(f"{base}/"):
        return None
    parsed = urlparse(str(url))
    return unquote(parsed.path.lstrip("/")) or None


def presigned_url(key: str, *, expires: int | None = None) -> str:
    expires = expires or int(get_setting("COS_SIGNED_URL_EXPIRES", "3600") or "3600")
    expires = max(60, min(expires, 7 * 24 * 3600))
    try:
        return str(
            _client().get_presigned_url(
                Bucket=get_cos_settings().bucket,
                Key=key,
                Method="GET",
                Expired=expires,
            )
        )
    except Exception as exc:
        raise CosStorageError(f"COS 签名 URL 生成失败：{key}：{exc}") from exc


def presigned_url_for_public_url(url: str) -> str | None:
    key = public_url_to_key(url)
    if not key:
        return None
    return presigned_url(key)


def upload_file(path: str | Path, key: str) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise CosStorageError(f"待上传文件不存在：{path}")

    content_type, _ = mimetypes.guess_type(path.name)
    kwargs: dict[str, Any] = {}
    if content_type:
        kwargs["ContentType"] = content_type

    try:
        with path.open("rb") as body:
            _client().put_object(
                Bucket=get_cos_settings().bucket,
                Key=key,
                Body=body,
                EnableMD5=False,
                **kwargs,
            )
    except Exception as exc:
        raise CosStorageError(f"COS 上传失败：{path.name} -> {key}：{exc}") from exc

    return {
        "key": key,
        "url": public_url(key),
        "size": path.stat().st_size,
        "content_type": content_type or "application/octet-stream",
    }


def upload_bytes(data: bytes, key: str, *, content_type: str = "application/octet-stream") -> dict[str, Any]:
    try:
        _client().put_object(
            Bucket=get_cos_settings().bucket,
            Key=key,
            Body=data,
            EnableMD5=False,
            ContentType=content_type,
        )
    except Exception as exc:
        raise CosStorageError(f"COS 上传失败：{key}：{exc}") from exc

    return {
        "key": key,
        "url": public_url(key),
        "size": len(data),
        "content_type": content_type,
    }


def upload_json(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return upload_bytes(
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        key,
        content_type="application/json; charset=utf-8",
    )


def load_json(key: str) -> dict[str, Any] | None:
    try:
        response = _client().get_object(Bucket=get_cos_settings().bucket, Key=key)
        data = response["Body"].get_raw_stream().read()
    except Exception:
        return None

    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "failed",
            "error": "COS 结果文件格式损坏，无法读取。",
        }
    return payload if isinstance(payload, dict) else None


def list_keys(prefix: str) -> list[str]:
    keys: list[str] = []
    marker = ""
    while True:
        try:
            response = _client().list_objects(
                Bucket=get_cos_settings().bucket,
                Prefix=prefix,
                Marker=marker,
                MaxKeys=1000,
            )
        except Exception as exc:
            raise CosStorageError(f"COS 列举对象失败：{prefix}：{exc}") from exc

        contents = response.get("Contents") or []
        if isinstance(contents, dict):
            contents = [contents]
        for item in contents:
            key = item.get("Key") if isinstance(item, dict) else None
            if key:
                keys.append(str(key))

        if str(response.get("IsTruncated", "")).lower() != "true":
            break
        marker = str(response.get("NextMarker") or (keys[-1] if keys else ""))
        if not marker:
            break
    return keys


def delete_keys(keys: Iterable[str]) -> int:
    unique_keys = [key for key in dict.fromkeys(str(key) for key in keys if str(key or "").strip())]
    deleted = 0
    for index in range(0, len(unique_keys), 1000):
        batch = unique_keys[index : index + 1000]
        if not batch:
            continue
        try:
            _client().delete_objects(
                Bucket=get_cos_settings().bucket,
                Delete={"Object": [{"Key": key} for key in batch], "Quiet": "true"},
            )
        except Exception as exc:
            raise CosStorageError(f"COS 删除对象失败：{exc}") from exc
        deleted += len(batch)
    return deleted


def delete_prefix(prefix: str) -> int:
    return delete_keys(list_keys(prefix))
