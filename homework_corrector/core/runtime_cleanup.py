from __future__ import annotations

import time
from pathlib import Path

from homework_corrector.core.config import CUT_DIR, PROCESSED_DIR, UPLOAD_DIR, ensure_runtime_dirs, get_int_setting


TRANSIENT_DIRS = (UPLOAD_DIR, PROCESSED_DIR, CUT_DIR)
DEFAULT_MAX_AGE_HOURS = 24
_last_cleanup_at = 0.0


def _runtime_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [path for path in directory.rglob("*") if path.is_file() and path.name != ".gitkeep"]


def _remove_empty_subdirectories(directory: Path) -> None:
    if not directory.exists():
        return

    subdirectories = sorted(
        (path for path in directory.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in subdirectories:
        try:
            path.rmdir()
        except OSError:
            pass


def cleanup_runtime_files(*, force: bool = False) -> dict[str, int]:
    global _last_cleanup_at

    ensure_runtime_dirs()
    now = time.time()
    interval_seconds = max(60, get_int_setting("CLEANUP_INTERVAL_SECONDS", 600))
    if not force and now - _last_cleanup_at < interval_seconds:
        return {"deleted": 0, "scanned": 0}

    _last_cleanup_at = now
    max_age_seconds = max(1, get_int_setting("CLEANUP_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS)) * 3600

    deleted = 0
    scanned = 0
    for directory in TRANSIENT_DIRS:
        files = _runtime_files(directory)
        scanned += len(files)

        for path in files:
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
                    deleted += 1
            except OSError:
                pass

        _remove_empty_subdirectories(directory)

    return {"deleted": deleted, "scanned": scanned}
