from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config import DATA_DIR, ensure_runtime_dirs
from time_utils import beijing_now


CAPTURE_TTL_MINUTES = 10
CAPTURE_PATH = DATA_DIR / "mobile_captures.json"
CAPTURE_IMAGE_DIR = DATA_DIR / "mobile_captures"
_capture_lock = threading.RLock()


class MobileCaptureError(RuntimeError):
    pass


class MobileCaptureImage:
    def __init__(self, path: str | Path, name: str | None = None) -> None:
        self.path = Path(path)
        self.name = name or self.path.name

    @property
    def size(self) -> int:
        return self.path.stat().st_size

    def getbuffer(self) -> memoryview:
        return memoryview(self.path.read_bytes())


def _now() -> datetime:
    return beijing_now()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_store() -> dict[str, Any]:
    ensure_runtime_dirs()
    if not CAPTURE_PATH.exists():
        return {"captures": []}
    try:
        payload = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"captures": []}
    captures = payload.get("captures")
    return {"captures": captures if isinstance(captures, list) else []}


def _write_store(payload: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    CAPTURE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = CAPTURE_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(CAPTURE_PATH)


def _is_expired(item: dict[str, Any], now: datetime | None = None) -> bool:
    expires_at = _parse_time(item.get("expires_at"))
    return expires_at is None or expires_at <= (now or _now())


def _cleanup_expired(payload: dict[str, Any], now: datetime | None = None) -> bool:
    current_time = now or _now()
    kept = []
    changed = False
    for item in payload.get("captures", []):
        if not isinstance(item, dict) or _is_expired(item, current_time):
            image_path = item.get("image_path") if isinstance(item, dict) else None
            if image_path:
                try:
                    Path(image_path).unlink(missing_ok=True)
                except OSError:
                    pass
            changed = True
            continue
        kept.append(item)
    payload["captures"] = kept
    return changed


def _find_capture(payload: dict[str, Any], token: str) -> dict[str, Any] | None:
    for item in payload.get("captures", []):
        if isinstance(item, dict) and item.get("token") == token:
            return item
    return None


def create_mobile_capture(owner_username: str, source_key: str) -> dict[str, Any]:
    owner_username = str(owner_username or "").strip().lower()
    if not owner_username:
        raise MobileCaptureError("手机拍照链接必须关联登录用户。")

    with _capture_lock:
        payload = _load_store()
        changed = _cleanup_expired(payload)
        existing_tokens = {item.get("token") for item in payload.get("captures", [])}
        token = secrets.token_urlsafe(24)
        while token in existing_tokens:
            token = secrets.token_urlsafe(24)

        created_at = _now()
        item = {
            "token": token,
            "owner_username": owner_username,
            "source_key": str(source_key),
            "created_at": _iso(created_at),
            "expires_at": _iso(created_at + timedelta(minutes=CAPTURE_TTL_MINUTES)),
            "uploaded_at": None,
            "image_path": None,
            "image_name": None,
            "used": False,
        }
        payload["captures"].append(item)
        _write_store(payload)
        return dict(item)


def get_mobile_capture_status(token: str) -> dict[str, Any] | None:
    with _capture_lock:
        payload = _load_store()
        changed = _cleanup_expired(payload)
        item = _find_capture(payload, token)
        if changed:
            _write_store(payload)
        return dict(item) if item else None


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return suffix
    return ".jpg"


def save_mobile_capture_upload(token: str, image_file: Any) -> dict[str, Any]:
    data = bytes(image_file.getbuffer())
    if not data:
        raise MobileCaptureError("没有收到手机图片。")

    with _capture_lock:
        payload = _load_store()
        changed = _cleanup_expired(payload)
        item = _find_capture(payload, token)
        if not item:
            if changed:
                _write_store(payload)
            raise MobileCaptureError("手机拍照链接已过期或不存在。")
        if item.get("used") or item.get("image_path"):
            raise MobileCaptureError("这条手机拍照链接已经使用过。")
        if _is_expired(item):
            _cleanup_expired(payload)
            _write_store(payload)
            raise MobileCaptureError("手机拍照链接已过期。")

        CAPTURE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        suffix = _safe_suffix(getattr(image_file, "name", None))
        image_path = CAPTURE_IMAGE_DIR / f"{token}{suffix}"
        image_path.write_bytes(data)

        item["image_path"] = str(image_path)
        item["image_name"] = f"mobile_{item.get('source_key')}_{token[:8]}{suffix}"
        item["uploaded_at"] = _iso(_now())
        item["used"] = True
        _write_store(payload)
        return dict(item)


def get_mobile_capture_image(token: str, owner_username: str) -> MobileCaptureImage | None:
    owner_username = str(owner_username or "").strip().lower()
    with _capture_lock:
        payload = _load_store()
        changed = _cleanup_expired(payload)
        item = _find_capture(payload, token)
        if changed:
            _write_store(payload)
        if not item or item.get("owner_username") != owner_username:
            return None
        image_path = item.get("image_path")
        if not image_path or not Path(image_path).exists():
            return None
        return MobileCaptureImage(image_path, item.get("image_name"))
